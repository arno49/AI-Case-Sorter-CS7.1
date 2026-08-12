/**
 * Which manual controls a page may offer, and every sentence beside them.
 *
 * Like `machine-status.ts`, this module exists so that no markup decides what
 * the operator is told. A control is offered or withheld here, from the
 * snapshot the operator is looking at, and a withheld control always carries
 * the reason in words this workspace owns — with one deliberate exception:
 * the firmware feed gate's reason and the daemon's readiness reason are passed
 * through verbatim, because paraphrasing an explanation the daemon wrote is
 * how an explanation drifts from the thing it explains.
 *
 * Withholding a control here is a courtesy to the operator, not the
 * authorization. Every submission is authorized again on the server, and the
 * daemon remains the authority on what the machine will actually accept: a
 * form this module enabled can still be refused, and that refusal reaches the
 * page in the error boundary's wording.
 *
 * `recoveryPlan` makes the same decision for recovery, which is a different
 * capability (`machine.recover`, administrator-only) and a different kind of
 * control: it tears the session down and starts again from a fresh
 * transport, so it is offered whenever a session isn't already being
 * established, not gated on a `READY` session the way motion is.
 */

import type { MachineSnapshot } from '$lib/machine';

/** One control: offered, or withheld with the sentence that says why. */
export interface ControlDecision {
	readonly enabled: boolean;
	readonly reason: string | null;
}

export interface SortControl extends ControlDecision {
	/** The slots the firmware advertises, each a valid choice for the form. */
	readonly slots: readonly number[];
}

export interface ControlsPlan {
	/** Whether this role may operate at all; nothing renders when false. */
	readonly offered: boolean;
	/** Set when no control can be prepared at all — there is no snapshot. */
	readonly withheld: string | null;
	/** The generation every command names: the machine the operator saw. */
	readonly generation: number | null;
	readonly connect: ControlDecision;
	readonly home: ControlDecision;
	readonly sort: SortControl;
	readonly feed: ControlDecision;
}

const NO_SNAPSHOT =
	'The machine has not been read, so no command can be prepared: every' +
	' command names the machine state it was decided against.';
const NO_SESSION = 'There is no controller session. Connect first.';
const ALREADY_CONNECTED = 'A controller session is already established.';
const SESSION_IN_PROGRESS = 'A controller session is already being established.';
const RECOVERY_IN_PROGRESS = 'Recovery is in progress.';
const UNCERTAIN_SESSION =
	'The machine state is not known. Recovery, not a new connection, is the way back from that.';
const HOME_UNADVERTISED = 'The controller does not advertise homing.';
const SORT_UNADVERTISED = 'The controller does not advertise sorting.';
const FEED_UNAVAILABLE = 'Feeding is not available on this machine.';

export function controlsPlan(
	snapshot: MachineSnapshot | null,
	capabilities: readonly string[]
): ControlsPlan {
	const offered = capabilities.includes('machine.operate');
	if (!offered || snapshot === null) {
		const nothing: ControlDecision = { enabled: false, reason: null };
		return {
			offered,
			withheld: offered ? NO_SNAPSHOT : null,
			generation: null,
			connect: nothing,
			home: nothing,
			sort: { ...nothing, slots: [] },
			feed: nothing
		};
	}
	return {
		offered,
		withheld: null,
		generation: snapshot.generation,
		connect: connectDecision(snapshot),
		home: motionDecision(
			snapshot,
			snapshot.firmware.capabilities.home_available,
			HOME_UNADVERTISED
		),
		sort: sortDecision(snapshot),
		feed: feedDecision(snapshot)
	};
}

/** What the page says beside an accepted command. Never a completion. */
export function acceptedWording(operationId: string): string {
	return `Accepted as operation ${operationId}. This is an acceptance, not a result — watch the machine.`;
}

/** Recovery/reset: a different capability, decided independently of `ControlsPlan`. */
export interface RecoveryPlan {
	readonly offered: boolean;
	readonly withheld: string | null;
	readonly generation: number | null;
	readonly decision: ControlDecision;
}

export function recoveryPlan(
	snapshot: MachineSnapshot | null,
	capabilities: readonly string[]
): RecoveryPlan {
	const offered = capabilities.includes('machine.recover');
	if (!offered || snapshot === null) {
		return {
			offered,
			withheld: offered ? NO_SNAPSHOT : null,
			generation: null,
			decision: { enabled: false, reason: null }
		};
	}
	return {
		offered,
		withheld: null,
		generation: snapshot.generation,
		decision: recoveryDecision(snapshot)
	};
}

function recoveryDecision(snapshot: MachineSnapshot): ControlDecision {
	switch (snapshot.connection_state) {
		case 'RECOVERING':
			return withheld(RECOVERY_IN_PROGRESS);
		case 'CONNECTING':
		case 'VERIFYING_V1':
		case 'ACTIVATING_V2':
			return withheld(SESSION_IN_PROGRESS);
		default:
			// READY, DISCONNECTED and UNCERTAIN all accept a recovery: it is the
			// way back from UNCERTAIN, and a deliberate reset in the others.
			return offeredControl();
	}
}

function connectDecision(snapshot: MachineSnapshot): ControlDecision {
	switch (snapshot.connection_state) {
		case 'READY':
			return withheld(ALREADY_CONNECTED);
		case 'UNCERTAIN':
			// Connecting over an unknown state would paper over it. The recovery
			// screen, which demands explicit confirmation, is the way back.
			return withheld(UNCERTAIN_SESSION);
		case 'RECOVERING':
			return withheld(RECOVERY_IN_PROGRESS);
		case 'CONNECTING':
		case 'VERIFYING_V1':
		case 'ACTIVATING_V2':
			return withheld(SESSION_IN_PROGRESS);
		default:
			return offeredControl();
	}
}

function sortDecision(snapshot: MachineSnapshot): SortControl {
	const decision = motionDecision(
		snapshot,
		snapshot.firmware.capabilities.sort_available,
		SORT_UNADVERTISED
	);
	if (!decision.enabled) {
		return { ...decision, slots: [] };
	}
	const count = snapshot.firmware.capabilities.slot_count;
	return { ...decision, slots: Array.from({ length: count }, (_, slot) => slot) };
}

function feedDecision(snapshot: MachineSnapshot): ControlDecision {
	const gate = motionGate(snapshot);
	if (gate !== null) {
		return withheld(gate);
	}
	if (snapshot.firmware.capabilities.feed_available) {
		return offeredControl();
	}
	const reported = snapshot.firmware.capabilities.feed_unavailable_reason;
	// The daemon's reason verbatim: it names the unqualified firmware gate,
	// and that name is the explanation the operator is owed.
	return withheld(
		reported ? `${FEED_UNAVAILABLE} The machine service reports: ${reported}` : FEED_UNAVAILABLE
	);
}

function motionDecision(
	snapshot: MachineSnapshot,
	advertised: boolean,
	unadvertised: string
): ControlDecision {
	const gate = motionGate(snapshot);
	if (gate !== null) {
		return withheld(gate);
	}
	return advertised ? offeredControl() : withheld(unadvertised);
}

/** Why no motion command can be offered, or `null` when one can. */
function motionGate(snapshot: MachineSnapshot): string | null {
	if (snapshot.connection_state !== 'READY') {
		return NO_SESSION;
	}
	if (!snapshot.ready) {
		const reason = snapshot.readiness_reason;
		return reason
			? `The machine service is not accepting commands: ${reason}`
			: 'The machine service is not accepting commands.';
	}
	return null;
}

function offeredControl(): ControlDecision {
	return { enabled: true, reason: null };
}

function withheld(reason: string): ControlDecision {
	return { enabled: false, reason };
}

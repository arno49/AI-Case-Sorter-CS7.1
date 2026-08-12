/**
 * What a snapshot of the machine is allowed to say on a screen.
 *
 * Every sentence an operator reads about a machine that can move is decided
 * here, in one place, as data rather than as markup. That is what makes the
 * rules testable: a screen cannot quietly start describing an acceptance as a
 * result, and an unobserved session cannot start reading as an observation,
 * because both are properties of this module and both have specs.
 *
 * Three rules run through all of it.
 *
 * Acceptance is not completion. The daemon accepting a command means it has
 * given the intent a durable identity, not that the machine did anything, so
 * nothing that has not settled may be described with a word that suggests it
 * did.
 *
 * A terminal state is worth what the controller confirmed. `trusted_terminal`
 * is the difference between an outcome and a guess, and where it is missing
 * this says the outcome is not known rather than repeating a state name as if
 * it were a fact.
 *
 * Not known is a thing worth saying. An operator acting on "not homed" will
 * home the machine; an operator acting on a confident wrong answer will not.
 */

import type {
	ConnectionState,
	Fault,
	FaultState,
	MachineSnapshot,
	Operation,
	OperationState,
	OperationType
} from '$lib/machine';

/**
 * How loudly a reading should be presented.
 *
 * `uncertain` is its own tone rather than a louder `attention`: a machine whose
 * state is not known is a different situation from one with a known problem,
 * and the two must not be able to collapse into each other.
 */
export type Tone = 'ordinary' | 'attention' | 'uncertain';

export interface Reading {
	readonly label: string;
	readonly detail: string | null;
	readonly tone: Tone;
}

/**
 * Words that may appear only about an operation that has settled.
 *
 * Kept here so the rule and its enforcement cannot drift apart: the specs use
 * this same pattern to check every summary this module can produce.
 */
export const COMPLETION_WORDS =
	/\b(complete|completed|completes|finish|finished|done|succeed|succeeded|success|successful|stopped)\b/i;

const CONNECTION_READINGS: Readonly<Record<ConnectionState, Reading>> = Object.freeze({
	DISCONNECTED: {
		label: 'Not connected',
		detail: 'There is no session with the controller.',
		tone: 'attention'
	},
	CONNECTING: {
		label: 'Connecting',
		detail: 'Opening a session with the controller.',
		tone: 'ordinary'
	},
	VERIFYING_V1: {
		label: 'Verifying the controller',
		detail: 'Checking what the controller answers before trusting it.',
		tone: 'ordinary'
	},
	ACTIVATING_V2: {
		label: 'Negotiating the protocol',
		detail: 'The controller answered; agreeing which protocol to speak.',
		tone: 'ordinary'
	},
	READY: {
		label: 'Connected',
		detail: 'The machine service has a verified session with the controller.',
		tone: 'ordinary'
	},
	RECOVERING: {
		label: 'Recovering',
		detail: 'The machine service is re-establishing what it knows.',
		tone: 'attention'
	},
	UNCERTAIN: {
		label: 'Not known',
		detail:
			'The machine service cannot say what the session is doing. Treat the machine as able to move.',
		tone: 'uncertain'
	}
});

const FAULT_TONES: Readonly<Record<FaultState, Tone>> = Object.freeze({
	CLEAR: 'ordinary',
	ACTIVE: 'attention',
	LATCHED: 'attention',
	UNCERTAIN: 'uncertain'
});

/** What each operation type is called. Shared with the history screen, so the
 *  two never drift into naming the same type two different ways. */
export const OPERATION_TITLES: Readonly<Record<OperationType, string>> = Object.freeze({
	CONNECT: 'Connect',
	RECOVER: 'Recover',
	STOP: 'Stop',
	HOME: 'Home',
	SORT: 'Sort',
	FEED: 'Feed',
	CONFIGURE: 'Configure'
});

const TERMINAL_STATES: ReadonlySet<OperationState> = new Set<OperationState>([
	'SUCCEEDED',
	'FAILED',
	'CANCELLED',
	'UNCERTAIN'
]);

/** Said of a terminal the controller confirmed, and of nothing else. */
const CONFIRMED = 'The controller confirmed it.';

/** Said of a terminal that arrived without the controller's confirmation. */
const UNCONFIRMED = 'The controller did not confirm it, so what the machine did is not known.';

export function connectionReading(snapshot: MachineSnapshot): Reading {
	return CONNECTION_READINGS[snapshot.connection_state];
}

/**
 * Whether the machine service will admit work — and nothing more.
 *
 * The daemon's readiness is about its own session and its own durability. It is
 * not a statement about physical clearance, homing or energy isolation, and the
 * detail says so every time rather than relying on an operator to remember it.
 */
export function readinessReading(snapshot: MachineSnapshot): Reading {
	if (snapshot.ready) {
		return {
			label: 'Work may be admitted',
			detail:
				'Session readiness only. It is not a statement about physical clearance, homing or energy isolation.',
			tone: 'ordinary'
		};
	}
	return {
		label: 'Work will not be admitted',
		detail: snapshot.readiness_reason ?? 'The machine service did not give a reason.',
		tone: 'attention'
	};
}

export type HomingState = 'homed' | 'not-homed' | 'not-known';

export interface HomingReading {
	readonly axis: string;
	readonly state: HomingState;
	readonly label: string;
	readonly tone: Tone;
}

/**
 * Homing, per axis, and only where it was observed.
 *
 * A snapshot reports an axis as not homed when the controller says so *and*
 * when the session has never observed the controller at all — the field is a
 * boolean and has no third value for that. So a `false` on its own cannot be
 * read as an observation, and this reports "not known" for any session that is
 * not connected rather than turning an absence of knowledge into a fact.
 *
 * The direction of that caution matters. An operator told "not known" homes the
 * machine, which is safe and repeatable; an operator told "homed" by a screen
 * that was guessing acts on a machine that has never found its ends.
 */
export function homingReadings(snapshot: MachineSnapshot): readonly HomingReading[] {
	const observed = snapshot.connection_state === 'READY';
	return [
		homing('Feeder', observed ? snapshot.machine.feed_homed : null),
		homing('Sorter', observed ? snapshot.machine.sort_homed : null)
	];
}

function homing(axis: string, homed: boolean | null): HomingReading {
	if (homed === null) {
		return {
			axis,
			state: 'not-known',
			label: 'Not known',
			tone: 'uncertain'
		};
	}
	return homed
		? { axis, state: 'homed', label: 'Homed', tone: 'ordinary' }
		: { axis, state: 'not-homed', label: 'Not homed', tone: 'attention' };
}

export interface FaultReading {
	readonly faultId: string;
	readonly code: string;
	readonly source: string;
	readonly state: FaultState;
	readonly message: string | null;
	readonly openedAt: string;
	readonly tone: Tone;
}

/** The headline: what state the faults are in, and how many are recorded. */
export function faultSummary(snapshot: MachineSnapshot): Reading {
	const count = snapshot.faults.length;
	return {
		label: FAULT_LABELS[snapshot.fault_state],
		detail: count === 0 ? 'None recorded.' : `${count} recorded.`,
		tone: FAULT_TONES[snapshot.fault_state]
	};
}

const FAULT_LABELS: Readonly<Record<FaultState, string>> = Object.freeze({
	CLEAR: 'Clear',
	ACTIVE: 'Active',
	LATCHED: 'Latched',
	UNCERTAIN: 'Not known'
});

export function faultReadings(snapshot: MachineSnapshot): readonly FaultReading[] {
	return snapshot.faults.map((fault: Fault) => ({
		faultId: fault.fault_id,
		code: fault.code,
		source: fault.source,
		state: fault.state,
		message: fault.message ?? null,
		openedAt: fault.opened_at,
		tone: FAULT_TONES[fault.state]
	}));
}

/**
 * How far an operation has got, as a screen is allowed to describe it.
 *
 * `settled` is the only value that permits a word of completion, and it is
 * reached only through a terminal the controller confirmed. `unsettled` is a
 * terminal state whose outcome is nonetheless not known.
 */
export type OperationProgress = 'pending' | 'running' | 'settled' | 'unsettled';

export interface OperationReading {
	readonly operationId: string;
	readonly title: string;
	readonly state: OperationState;
	readonly progress: OperationProgress;
	readonly summary: string;
	readonly trustedTerminal: boolean;
	readonly tone: Tone;
}

export function operationReading(operation: Operation): OperationReading {
	const title = OPERATION_TITLES[operation.type] ?? operation.type;
	const common = {
		operationId: operation.operation_id,
		title,
		state: operation.state,
		trustedTerminal: operation.trusted_terminal
	};

	if (!TERMINAL_STATES.has(operation.state)) {
		return { ...common, ...inFlight(operation.state) };
	}

	if (operation.state === 'UNCERTAIN') {
		return {
			...common,
			progress: 'unsettled',
			summary:
				'The outcome is not known. Treat the machine as able to move until it has been checked.',
			tone: 'uncertain'
		};
	}

	if (!operation.trusted_terminal) {
		// A `SUCCEEDED` without a trusted terminal is a shape the contract forbids.
		// It is described as unknown rather than trusted or dropped: an answer this
		// service cannot account for is not one to repeat back as a result, and
		// naming the state would be repeating it.
		const named =
			operation.state === 'SUCCEEDED'
				? 'The machine service reported a terminal state.'
				: `${TERMINAL_LABELS[operation.state]}.`;
		return {
			...common,
			progress: 'unsettled',
			summary: `${named} ${UNCONFIRMED}`,
			tone: 'uncertain'
		};
	}

	return {
		...common,
		progress: 'settled',
		summary: `${TERMINAL_LABELS[operation.state]}. ${CONFIRMED}`,
		tone: operation.state === 'SUCCEEDED' ? 'ordinary' : 'attention'
	};
}

const TERMINAL_LABELS: Readonly<Record<string, string>> = Object.freeze({
	SUCCEEDED: 'Completed',
	FAILED: 'Failed',
	CANCELLED: 'Cancelled'
});

function inFlight(state: OperationState): {
	progress: OperationProgress;
	summary: string;
	tone: Tone;
} {
	if (state === 'RUNNING') {
		return {
			progress: 'running',
			summary: 'Running on the machine. No outcome has been reported.',
			tone: 'attention'
		};
	}
	if (state === 'QUEUED') {
		return {
			progress: 'pending',
			summary: 'Queued by the machine service. The machine has not been asked to act yet.',
			tone: 'ordinary'
		};
	}
	return {
		progress: 'pending',
		summary:
			'Accepted by the machine service. This is an acceptance, not a result — the machine may still be moving.',
		tone: 'ordinary'
	};
}

export function activeOperationReading(snapshot: MachineSnapshot): OperationReading | null {
	return snapshot.active_operation ? operationReading(snapshot.active_operation) : null;
}

/**
 * What may be offered as a manual control, and what is said when it is not.
 *
 * The decisions under test are the safety-relevant ones: a command is only
 * offered against a machine state the operator has actually seen, a firmware
 * capability that is not advertised is not offered, and the feed control
 * carries the daemon's own reason for the unqualified gate rather than a
 * paraphrase of it.
 */

import { describe, expect, it } from 'vitest';

import type { MachineSnapshot } from '$lib/machine';
import { acceptedWording, controlsPlan, recoveryPlan } from '$lib/machine-controls';
import { COMPLETION_WORDS } from '$lib/machine-status';

const OPERATOR = ['machine.read', 'machine.stop', 'machine.operate'];
const VIEWER = ['machine.read', 'machine.stop'];
const ADMINISTRATOR = [...OPERATOR, 'machine.recover', 'config.write', 'users.manage'];

function snapshot(
	overrides: Partial<MachineSnapshot> = {},
	capabilities: Record<string, unknown> = {}
): MachineSnapshot {
	return {
		api_version: 'v1',
		generation: 41,
		connection_state: 'READY',
		fault_state: 'CLEAR',
		ready: true,
		readiness_reason: null,
		active_operation: null,
		firmware: {
			firmware_version: null,
			protocol_version: 2,
			capabilities: {
				v2_available: true,
				crc_active: false,
				slot_count: 8,
				home_available: true,
				sort_available: true,
				feed_available: false,
				feed_unavailable_reason: 'the v2 feed lifecycle gate is NOT_EXECUTED',
				...capabilities
			}
		},
		machine: { feed_homed: true, sort_homed: true, sorter_slot: null },
		faults: [],
		observed_at: '2026-08-11T12:00:00.000Z',
		...overrides
	} as MachineSnapshot;
}

describe('who is offered manual controls', () => {
	it('offers nothing to a role without machine.operate', () => {
		const plan = controlsPlan(snapshot(), VIEWER);

		expect(plan.offered).toBe(false);
	});

	it('offers nothing against a machine that has not been read', () => {
		// A command names the generation it was decided against. With no
		// snapshot there is no such thing, so there is no command to prepare.
		const plan = controlsPlan(null, OPERATOR);

		expect(plan.offered).toBe(true);
		expect(plan.generation).toBeNull();
		expect(plan.withheld).toContain('has not been read');
		expect(plan.home.enabled).toBe(false);
		expect(plan.sort.enabled).toBe(false);
	});
});

describe('what a ready machine is offered', () => {
	it('offers home and sort, naming the generation the operator saw', () => {
		const plan = controlsPlan(snapshot(), OPERATOR);

		expect(plan.generation).toBe(41);
		expect(plan.home).toEqual({ enabled: true, reason: null });
		expect(plan.sort.enabled).toBe(true);
	});

	it('offers exactly the slots the firmware advertises', () => {
		const plan = controlsPlan(snapshot(), OPERATOR);

		expect(plan.sort.slots).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
	});

	it('withholds connect when a session is already established', () => {
		const plan = controlsPlan(snapshot(), OPERATOR);

		expect(plan.connect.enabled).toBe(false);
		expect(plan.connect.reason).toContain('already established');
	});

	it('withholds an axis the controller does not advertise', () => {
		const view = snapshot({}, { home_available: false, sort_available: false });

		const plan = controlsPlan(view, OPERATOR);

		expect(plan.home.enabled).toBe(false);
		expect(plan.home.reason).toContain('does not advertise');
		expect(plan.sort.enabled).toBe(false);
		expect(plan.sort.slots).toEqual([]);
	});
});

describe('the feed gate', () => {
	it('withholds feed with the daemon reason verbatim while the gate is unqualified', () => {
		const plan = controlsPlan(snapshot(), OPERATOR);

		expect(plan.feed.enabled).toBe(false);
		expect(plan.feed.reason).toContain('the v2 feed lifecycle gate is NOT_EXECUTED');
	});

	it('is driven by the capability, not by this module knowing better', () => {
		// If a qualified firmware ever advertises feeding, this module follows
		// the snapshot. Withholding it forever here would make the gate a UI
		// opinion instead of firmware evidence.
		const view = snapshot({}, { feed_available: true, feed_unavailable_reason: undefined });

		const plan = controlsPlan(view, OPERATOR);

		expect(plan.feed).toEqual({ enabled: true, reason: null });
	});
});

describe('a machine without a working session', () => {
	it('offers connect and withholds motion when disconnected', () => {
		const view = snapshot({ connection_state: 'DISCONNECTED', ready: false });

		const plan = controlsPlan(view, OPERATOR);

		expect(plan.connect.enabled).toBe(true);
		expect(plan.home.enabled).toBe(false);
		expect(plan.home.reason).toContain('Connect first');
		expect(plan.sort.enabled).toBe(false);
		expect(plan.feed.enabled).toBe(false);
	});

	it('withholds connect over an unknown state and points at recovery', () => {
		// Reconnecting over UNCERTAIN would paper over a machine that may have
		// moved. The only way back is the recovery flow, which demands explicit
		// confirmation.
		const view = snapshot({ connection_state: 'UNCERTAIN', ready: false });

		const plan = controlsPlan(view, OPERATOR);

		expect(plan.connect.enabled).toBe(false);
		expect(plan.connect.reason).toContain('Recovery');
	});

	it('passes the daemon readiness reason through when commands are refused', () => {
		const view = snapshot({ ready: false, readiness_reason: 'journal unavailable' });

		const plan = controlsPlan(view, OPERATOR);

		expect(plan.home.enabled).toBe(false);
		expect(plan.home.reason).toContain('journal unavailable');
	});

	it('withholds every motion control when the session state is not known', () => {
		// UNCERTAIN is exactly the case a guessed capability must not paper over:
		// the wire has not told this module anything trustworthy about what the
		// controller can do right now.
		const view = snapshot({ connection_state: 'UNCERTAIN', ready: false });

		const plan = controlsPlan(view, OPERATOR);

		expect(plan.home.enabled).toBe(false);
		expect(plan.sort.enabled).toBe(false);
		expect(plan.feed.enabled).toBe(false);
	});
});

describe('who is offered recovery', () => {
	it('offers nothing to a role without machine.recover', () => {
		const plan = recoveryPlan(snapshot(), OPERATOR);

		expect(plan.offered).toBe(false);
	});

	it('offers nothing against a machine that has not been read', () => {
		const plan = recoveryPlan(null, ADMINISTRATOR);

		expect(plan.offered).toBe(true);
		expect(plan.generation).toBeNull();
		expect(plan.withheld).toContain('has not been read');
		expect(plan.decision.enabled).toBe(false);
	});
});

describe('when recovery may be attempted', () => {
	it('is the way back from a session that is not known', () => {
		const view = snapshot({ connection_state: 'UNCERTAIN', ready: false });

		const plan = recoveryPlan(view, ADMINISTRATOR);

		expect(plan.decision).toEqual({ enabled: true, reason: null });
		expect(plan.generation).toBe(41);
	});

	it('is offered as a deliberate reset of an otherwise healthy session', () => {
		const plan = recoveryPlan(snapshot(), ADMINISTRATOR);

		expect(plan.decision.enabled).toBe(true);
	});

	it('is withheld while a session is already being established or recovered', () => {
		for (const connection_state of ['CONNECTING', 'VERIFYING_V1', 'ACTIVATING_V2'] as const) {
			const plan = recoveryPlan(snapshot({ connection_state }), ADMINISTRATOR);
			expect(plan.decision.enabled).toBe(false);
		}

		const recovering = recoveryPlan(snapshot({ connection_state: 'RECOVERING' }), ADMINISTRATOR);
		expect(recovering.decision.enabled).toBe(false);
		expect(recovering.decision.reason).toContain('Recovery is in progress');
	});
});

describe('what an acceptance may be called', () => {
	it('names the operation without ever naming a completion', () => {
		const wording = acceptedWording('0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c');

		expect(wording).toContain('0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c');
		expect(wording).toContain('acceptance');
		expect(wording).not.toMatch(COMPLETION_WORDS);
	});
});

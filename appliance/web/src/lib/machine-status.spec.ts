/**
 * The sentences a screen may say about a machine that can move.
 *
 * Three rules, each with the case that would break it: acceptance is never
 * described as completion, a terminal without the controller's confirmation is
 * never described as an outcome, and an unobserved session is never described
 * as an observation.
 */

import { describe, expect, it } from 'vitest';

import type { MachineSnapshot, Operation } from '$lib/machine';
import {
	COMPLETION_WORDS,
	activeOperationReading,
	connectionReading,
	faultReadings,
	faultSummary,
	homingReadings,
	operationReading,
	readinessReading
} from './machine-status';

function snapshot(overrides: Partial<MachineSnapshot> = {}): MachineSnapshot {
	return {
		api_version: 'v1',
		generation: 7,
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
				feed_unavailable_reason: 'the v2 feed lifecycle gate is NOT_EXECUTED'
			}
		},
		machine: { feed_homed: true, sort_homed: true, sorter_slot: null },
		faults: [],
		observed_at: '2026-08-11T12:00:00.000Z',
		...overrides
	} as MachineSnapshot;
}

function operation(overrides: Partial<Operation> = {}): Operation {
	return {
		api_version: 'v1',
		operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
		type: 'SORT',
		state: 'ACCEPTED',
		actor: { user_id: 'u', role: 'operator' },
		created_at: '2026-08-11T12:00:00.000Z',
		deadline_at: '2026-08-11T12:00:15.000Z',
		generation: 7,
		trusted_terminal: false,
		...overrides
	} as Operation;
}

describe('what may be said about an operation', () => {
	it('describes an acceptance as an acceptance, never as a result', () => {
		for (const state of ['QUEUED', 'ACCEPTED', 'RUNNING'] as const) {
			const reading = operationReading(operation({ state }));

			expect(reading.progress).not.toBe('settled');
			expect(reading.summary).not.toMatch(COMPLETION_WORDS);
		}
	});

	it('says a confirmed success completed, in those words', () => {
		const reading = operationReading(
			operation({
				state: 'SUCCEEDED',
				trusted_terminal: true,
				terminal_at: '2026-08-11T12:00:05.000Z',
				outcome: 'COMPLETED'
			})
		);

		expect(reading.progress).toBe('settled');
		expect(reading.summary).toContain('Completed');
		expect(reading.summary).toContain('controller confirmed');
	});

	it('does not let a terminal without a trusted confirmation read as an outcome', () => {
		// The daemon reported FAILED but the controller never confirmed a terminal.
		// The one honest sentence is that what the machine did is not known.
		const reading = operationReading(
			operation({
				state: 'FAILED',
				trusted_terminal: false,
				terminal_at: '2026-08-11T12:00:05.000Z',
				outcome: 'FAILED'
			})
		);

		expect(reading.progress).toBe('unsettled');
		expect(reading.tone).toBe('uncertain');
		expect(reading.summary).toContain('not known');
	});

	it('refuses to repeat a success the contract forbids', () => {
		// SUCCEEDED requires trusted_terminal. A daemon that sends the pair anyway
		// is describing an answer this service cannot account for, and the word
		// "succeeded" must not survive the translation.
		const reading = operationReading(
			operation({ state: 'SUCCEEDED', trusted_terminal: false, outcome: 'COMPLETED' })
		);

		expect(reading.progress).toBe('unsettled');
		expect(reading.summary).not.toMatch(COMPLETION_WORDS);
		expect(reading.summary).toContain('not known');
	});

	it('treats an UNCERTAIN operation as a machine that may still move', () => {
		const reading = operationReading(
			operation({ state: 'UNCERTAIN', outcome: 'UNCERTAIN', trusted_terminal: false })
		);

		expect(reading.tone).toBe('uncertain');
		expect(reading.summary).toContain('able to move');
	});

	it('keeps the daemon operation id, unrenamed', () => {
		const reading = operationReading(operation());

		expect(reading.operationId).toBe('0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c');
	});
});

describe('what may be said about homing', () => {
	it('reports what the controller said while the session is connected', () => {
		const readings = homingReadings(
			snapshot({ machine: { feed_homed: true, sort_homed: false, sorter_slot: null } })
		);

		expect(readings).toMatchObject([
			{ axis: 'Feeder', state: 'homed' },
			{ axis: 'Sorter', state: 'not-homed' }
		]);
	});

	it('says "not known" rather than "not homed" when nothing was observed', () => {
		// The wire has no third value: an unobserved axis and an unhomed axis both
		// arrive as `false`. Presenting the first as the second would be a guess in
		// the safe-looking direction, which is still a guess.
		const readings = homingReadings(
			snapshot({
				connection_state: 'DISCONNECTED',
				ready: false,
				readiness_reason: 'no session',
				machine: { feed_homed: false, sort_homed: false, sorter_slot: null }
			})
		);

		for (const axis of readings) {
			expect(axis.state).toBe('not-known');
			expect(axis.tone).toBe('uncertain');
		}
	});
});

describe('what may be said about readiness', () => {
	it('never lets readiness read as physical safety', () => {
		const reading = readinessReading(snapshot({ ready: true }));

		expect(reading.detail).toContain('not a statement about physical clearance');
	});

	it('gives the daemon-supplied reason when work will not be admitted', () => {
		const reading = readinessReading(
			snapshot({ ready: false, readiness_reason: 'journal unavailable' })
		);

		expect(reading.label).toContain('not be admitted');
		expect(reading.detail).toBe('journal unavailable');
		expect(reading.tone).toBe('attention');
	});
});

describe('what may be said about the session and faults', () => {
	it('gives UNCERTAIN its own tone, so it cannot collapse into ordinary attention', () => {
		expect(connectionReading(snapshot({ connection_state: 'UNCERTAIN' })).tone).toBe('uncertain');
		expect(connectionReading(snapshot({ connection_state: 'DISCONNECTED' })).tone).toBe(
			'attention'
		);
		expect(faultSummary(snapshot({ fault_state: 'UNCERTAIN' })).tone).toBe('uncertain');
		expect(faultSummary(snapshot({ fault_state: 'LATCHED' })).tone).toBe('attention');
	});

	it('counts recorded faults and carries each with its own words', () => {
		const view = snapshot({
			fault_state: 'LATCHED',
			faults: [
				{
					fault_id: '9d6f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
					state: 'LATCHED',
					code: 'JOURNAL_UNAVAILABLE',
					source: 'journal',
					message: 'the journal is unavailable',
					opened_at: '2026-08-11T12:00:00.000Z'
				}
			]
		});

		expect(faultSummary(view).detail).toBe('1 recorded.');
		expect(faultReadings(view)).toMatchObject([
			{ code: 'JOURNAL_UNAVAILABLE', source: 'journal', tone: 'attention' }
		]);
	});

	it('reads the active operation off the snapshot, or nothing at all', () => {
		expect(activeOperationReading(snapshot())).toBeNull();
		expect(
			activeOperationReading(snapshot({ active_operation: operation({ state: 'RUNNING' }) }))
		).toMatchObject({ progress: 'running' });
	});
});

import { describe, expect, it } from 'vitest';

import { dtrGateReading, firmwareVersionReading, journalReading } from './system-view';
import type { MachineSnapshot, System } from './machine';

function snapshot(overrides: Partial<MachineSnapshot> = {}): MachineSnapshot {
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
				feed_unavailable_reason: 'the v2 feed lifecycle gate is NOT_EXECUTED'
			}
		},
		machine: { feed_homed: true, sort_homed: true, sorter_slot: null },
		faults: [],
		observed_at: '2026-08-11T12:00:00.000Z',
		...overrides
	} as MachineSnapshot;
}

function system(overrides: Partial<System> = {}): System {
	return {
		api_version: 'v1',
		dtr_gate_status: 'NOT_EXECUTED',
		observed_at: '2026-08-11T12:00:00.000Z',
		...overrides
	} as System;
}

describe('the DTR-gate reading', () => {
	it('never presents NOT_EXECUTED as a pass', () => {
		const reading = dtrGateReading(system());

		expect(reading.label).toBe('NOT_EXECUTED');
		expect(reading.detail).toContain('not a pass');
		expect(reading.tone).toBe('attention');
	});

	it('does not vouch for a value it has never seen', () => {
		const reading = dtrGateReading(system({ dtr_gate_status: 'SOMETHING_NEW' }));

		expect(reading.label).toBe('SOMETHING_NEW');
		expect(reading.detail).toContain('not independently verified');
		expect(reading.tone).toBe('uncertain');
	});
});

describe('the firmware version reading', () => {
	it('says not reported rather than a blank value when the controller gave none', () => {
		const reading = firmwareVersionReading(snapshot());

		expect(reading.label).toBe('Not reported');
		expect(reading.detail).toContain('Protocol version 2');
	});

	it('shows the version the controller reported', () => {
		const reading = firmwareVersionReading(
			snapshot({ firmware: { firmware_version: '7.1.260714.6', protocol_version: 2 } } as never)
		);

		expect(reading.label).toBe('7.1.260714.6');
	});
});

describe('the journal reading', () => {
	it('says it is inferred from faults, not a dedicated health check', () => {
		const reading = journalReading(snapshot());

		expect(reading.label).toBe('Clear');
		expect(reading.detail).toContain('Inferred from recorded faults');
	});

	it('reflects a recorded fault the same way the dashboard does', () => {
		const reading = journalReading(
			snapshot({
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
			})
		);

		expect(reading.label).toBe('Latched');
		expect(reading.tone).toBe('attention');
	});
});

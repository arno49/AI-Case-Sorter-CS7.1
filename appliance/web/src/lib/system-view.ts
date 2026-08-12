/**
 * What the system view may say about facts that are not per-session.
 *
 * Firmware version and journal health are both read from the snapshot the
 * dashboard already has, not modeled twice. The DTR-gate status is the one
 * new fact this screen adds, and it is presented the way
 * `docs/architecture/README.md`'s own status legend defines it: `NOT_EXECUTED`
 * is a documented evidence-status label, and this module never turns it, or
 * any value this daemon build might report, into a claim that the gate
 * passed. Storage health has no data source at all yet, and the screen says
 * so rather than fabricating one.
 */

import { faultSummary, type Reading } from '$lib/machine-status';
import type { MachineSnapshot, System } from '$lib/machine';

const NOT_EXECUTED_DETAIL =
	'This is not a pass. A POSIX/Linux real serial port stays refused until this' +
	' gate is closed with hardware evidence.';

/** A value this module does not recognise: reported, but not vouched for. */
const UNRECOGNISED_DETAIL =
	'The daemon reported this value; it is not independently verified by this screen.';

export const NOT_REPORTED_STORAGE = 'Not reported by this service.';

export function dtrGateReading(system: System): Reading {
	if (system.dtr_gate_status === 'NOT_EXECUTED') {
		return { label: system.dtr_gate_status, detail: NOT_EXECUTED_DETAIL, tone: 'attention' };
	}
	// Nothing this module has ever seen: presented as not known rather than
	// assumed safe, the same caution `machine-status.ts` applies to homing.
	return { label: system.dtr_gate_status, detail: UNRECOGNISED_DETAIL, tone: 'uncertain' };
}

export function firmwareVersionReading(snapshot: MachineSnapshot): Reading {
	const version = snapshot.firmware.firmware_version ?? null;
	return {
		label: version ?? 'Not reported',
		detail: `Protocol version ${snapshot.firmware.protocol_version}.`,
		tone: 'ordinary'
	};
}

/** Journal health as recorded faults let it be inferred, not a dedicated check. */
export function journalReading(snapshot: MachineSnapshot): Reading {
	const summary = faultSummary(snapshot);
	return {
		...summary,
		detail: `${summary.detail} Inferred from recorded faults, not a dedicated journal health check.`
	};
}

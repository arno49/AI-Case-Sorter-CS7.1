/**
 * What the dataset review screen may say about training readiness.
 *
 * ADR-0013 and PI-VISION-003's own backlog entry are specific about the
 * shape this has to take: the example count per class next to the configured
 * floor, a class below the floor visibly marked ineligible *with the reason
 * shown*, not merely absent from a list, and readiness stated before any
 * training control exists to gate (that control is PI-VISION-004/005 - this
 * screen has nothing to disable yet, only something to make visible ahead of
 * it).
 */

import type { Tone } from '$lib/machine-status';
import type { DatasetClassSummary, DatasetSummary } from '$lib/dataset';

export interface ClassReading {
	readonly slot: number;
	readonly count: number;
	readonly minimum: number;
	readonly eligible: boolean;
	readonly label: string;
	readonly detail: string;
	readonly tone: Tone;
}

/** Every class the dataset has examples for, slot order, floor applied. */
export function classReadings(summary: DatasetSummary): readonly ClassReading[] {
	return summary.classes
		.slice()
		.sort((a, b) => a.slot - b.slot)
		.map((entry) => classReading(entry, summary.minimumExamplesPerClass));
}

function classReading(entry: DatasetClassSummary, minimum: number): ClassReading {
	return {
		slot: entry.slot,
		count: entry.count,
		minimum,
		eligible: entry.eligible,
		label: `${entry.count} example${entry.count === 1 ? '' : 's'}`,
		detail: entry.eligible
			? `Meets the ${minimum}-example floor for training.`
			: `Below the ${minimum}-example floor; excluded from training until it clears.`,
		tone: entry.eligible ? 'ordinary' : 'attention'
	};
}

const NO_CLASSES_DETAIL =
	'No examples recorded yet. Sorting manually with cs71-vision running builds this dataset.';

/** The one sentence this screen leads with, above the per-class list. */
export function trainingReadinessDetail(summary: DatasetSummary): string {
	if (summary.classes.length === 0) {
		return NO_CLASSES_DETAIL;
	}
	return summary.trainingReady
		? 'At least one class has cleared the floor. Training is not offered from this screen yet.'
		: 'No class has cleared the floor yet; training cannot be offered.';
}

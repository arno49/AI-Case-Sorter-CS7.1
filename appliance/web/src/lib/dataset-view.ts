/**
 * What the dataset review screen may say about training readiness and the
 * recorded model versions.
 *
 * ADR-0013 and PI-VISION-003's own backlog entry are specific about the
 * dataset half of this: the example count per class next to the configured
 * floor, a class below the floor visibly marked ineligible *with the reason
 * shown*, not merely absent from a list. PI-VISION-005 adds the model half:
 * every recorded candidate's per-class accuracy shown next to the currently
 * active model's own, so "activation is refused unless the operator has
 * been shown the candidate's accuracy alongside the currently active
 * model's" (ADR-0013) is a property of what this page renders, not a
 * separate confirmation step bolted on afterward.
 */

import type { Tone } from '$lib/machine-status';
import type {
	CandidateSummary,
	DatasetClassSummary,
	DatasetSummary,
	ModelsSummary
} from '$lib/dataset';

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
		? 'At least one class has cleared the floor. Training can be started below.'
		: 'No class has cleared the floor yet; training cannot be started.';
}

export interface AccuracyComparisonRow {
	readonly slot: number;
	/** Null when this candidate excluded the class, or was never evaluated on it. */
	readonly candidateAccuracy: number | null;
	/** Null when the active model excluded the class, or nothing is active yet. */
	readonly activeAccuracy: number | null;
}

export interface ModelReading {
	readonly version: number;
	readonly trainedAt: string;
	readonly active: boolean;
	readonly includedClasses: readonly number[];
	readonly excludedClasses: readonly number[];
	/** Every class either this candidate or the active model has an accuracy for. */
	readonly accuracyComparison: readonly AccuracyComparisonRow[];
}

/** Every recorded candidate, newest first, each compared against the active model. */
export function modelReadings(summary: ModelsSummary): readonly ModelReading[] {
	const active =
		summary.candidates.find((candidate) => candidate.version === summary.activeVersion) ?? null;
	return summary.candidates
		.slice()
		.sort((a, b) => b.version - a.version)
		.map((candidate) => modelReading(candidate, active, summary.activeVersion));
}

function modelReading(
	candidate: CandidateSummary,
	active: CandidateSummary | null,
	activeVersion: number | null
): ModelReading {
	const slots = new Set<number>([
		...Object.keys(candidate.accuracyByClass).map(Number),
		...(active === null ? [] : Object.keys(active.accuracyByClass).map(Number))
	]);
	const accuracyComparison = [...slots]
		.sort((a, b) => a - b)
		.map((slot) => ({
			slot,
			candidateAccuracy: candidate.accuracyByClass[slot] ?? null,
			activeAccuracy: active?.accuracyByClass[slot] ?? null
		}));
	return {
		version: candidate.version,
		trainedAt: candidate.trainedAt,
		active: candidate.version === activeVersion,
		includedClasses: candidate.includedClasses,
		excludedClasses: candidate.excludedClasses,
		accuracyComparison
	};
}

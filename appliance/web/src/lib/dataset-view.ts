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
 * separate confirmation step bolted on afterward. PI-VISION-006 adds a
 * third kind of accuracy - live, measured against what the operator
 * actually chose after a suggestion was shown - kept visibly distinct from
 * the other two, since it answers a different question ("does this model
 * work in practice") than either of them.
 */

import type { Tone } from '$lib/machine-status';
import type {
	AutonomySummary,
	CandidateSummary,
	DatasetClassSummary,
	DatasetSummary,
	ModelsSummary,
	SuggestionAccuracy
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

/**
 * Live suggestion accuracy, as a sentence - not a percentage alone, which
 * would read as a claim even at `0/0`.
 */
export function suggestionAccuracyDetail(accuracy: SuggestionAccuracy): string {
	if (accuracy.total === 0) {
		return 'No suggestion has been matched to an operators choice yet.';
	}
	const percent = Math.round((accuracy.accuracy ?? 0) * 100);
	const noun = accuracy.total === 1 ? 'sort' : 'sorts';
	return `${percent}% (${accuracy.correct} of ${accuracy.total} ${noun} matched the suggestion shown at the time).`;
}

export interface AutonomyClassReading {
	readonly slot: number;
	/** Null when this class has no autonomy threshold configured. */
	readonly threshold: number | null;
	readonly detail: string;
	readonly tone: Tone;
}

/**
 * Every class with either a configured threshold or reviewed autonomous
 * activity, slot order (PI-VISION-008).
 *
 * A class with attempts but zero reviews yet is deliberately absent from
 * `summary.accuracyByClass` (`cs71vision.dataset.autonomous_accuracy_by_class`'s
 * own contract) - "unreviewed" and "reviewed clean" must never look the
 * same, so this never fabricates a reading for one.
 */
export function autonomyClassReadings(summary: AutonomySummary): readonly AutonomyClassReading[] {
	const slots = new Set<number>([
		...Object.keys(summary.thresholds).map(Number),
		...Object.keys(summary.accuracyByClass).map(Number)
	]);
	return [...slots].sort((a, b) => a - b).map((slot) => autonomyClassReading(slot, summary));
}

function autonomyClassReading(slot: number, summary: AutonomySummary): AutonomyClassReading {
	const threshold = summary.thresholds[slot] ?? null;
	const accuracy = summary.accuracyByClass[slot];
	if (accuracy === undefined) {
		return {
			slot,
			threshold,
			detail:
				threshold === null
					? 'No autonomy threshold configured; every suggestion for this class is held for confirmation.'
					: 'No autonomous attempt has been reviewed yet.',
			tone: 'ordinary'
		};
	}
	const falsePercent = Math.round((accuracy.falseRate ?? 0) * 100);
	return {
		slot,
		threshold,
		detail: `${falsePercent}% false (${accuracy.total - accuracy.correct} of ${accuracy.total} reviewed autonomous sorts were wrong).`,
		tone: (accuracy.falseRate ?? 0) > 0 ? 'attention' : 'ordinary'
	};
}

/** One pending review, phrased for a person deciding correct or incorrect. */
export function pendingReviewDetail(slot: number, attemptedAt: string): string {
	return `Slot ${slot}, attempted ${attemptedAt}.`;
}

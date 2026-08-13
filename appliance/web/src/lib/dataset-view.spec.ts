import { describe, expect, it } from 'vitest';

import {
	autonomyClassReadings,
	classReadings,
	modelReadings,
	pendingReviewDetail,
	suggestionAccuracyDetail,
	trainingReadinessDetail
} from './dataset-view';
import type { AutonomySummary, CandidateSummary, DatasetSummary, ModelsSummary } from './dataset';

function summary(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
	return {
		minimumExamplesPerClass: 40,
		classes: [
			{ slot: 5, count: 12, eligible: false },
			{ slot: 3, count: 52, eligible: true }
		],
		trainingReady: true,
		...overrides
	};
}

describe('classReadings', () => {
	it('sorts by slot, not by insertion order', () => {
		const readings = classReadings(summary());

		expect(readings.map((reading) => reading.slot)).toEqual([3, 5]);
	});

	it('marks a class below the floor ineligible with the reason shown', () => {
		const [, below] = classReadings(summary());

		expect(below.eligible).toBe(false);
		expect(below.tone).toBe('attention');
		expect(below.detail).toContain('Below the 40-example floor');
	});

	it('marks a class at or above the floor eligible', () => {
		const [atOrAbove] = classReadings(summary());

		expect(atOrAbove.eligible).toBe(true);
		expect(atOrAbove.tone).toBe('ordinary');
		expect(atOrAbove.detail).toContain('Meets the 40-example floor');
	});

	it('carries the count and the configured floor for display', () => {
		const [atOrAbove] = classReadings(summary());

		expect(atOrAbove.count).toBe(52);
		expect(atOrAbove.minimum).toBe(40);
		expect(atOrAbove.label).toBe('52 examples');
	});

	it('uses the singular for exactly one example', () => {
		const [reading] = classReadings(summary({ classes: [{ slot: 1, count: 1, eligible: false }] }));

		expect(reading.label).toBe('1 example');
	});
});

describe('trainingReadinessDetail', () => {
	it('says no examples are recorded yet when the dataset is empty', () => {
		expect(trainingReadinessDetail(summary({ classes: [], trainingReady: false }))).toContain(
			'No examples recorded yet'
		);
	});

	it('says a class has cleared the floor when training is ready', () => {
		expect(trainingReadinessDetail(summary({ trainingReady: true }))).toContain(
			'has cleared the floor'
		);
	});

	it('says no class has cleared the floor when training is not ready but classes exist', () => {
		const detail = trainingReadinessDetail(
			summary({
				classes: [{ slot: 1, count: 2, eligible: false }],
				trainingReady: false
			})
		);

		expect(detail).toContain('No class has cleared the floor yet');
	});
});

function candidate(overrides: Partial<CandidateSummary> = {}): CandidateSummary {
	return {
		version: 1,
		trainedAt: '2026-08-12T12:00:00.000Z',
		includedClasses: [3, 5],
		excludedClasses: [7],
		accuracyByClass: { 3: 1.0, 5: 0.9 },
		minimumExamplesPerClass: 40,
		trainingExampleCount: 64,
		holdoutExampleCount: 16,
		...overrides
	};
}

function models(overrides: Partial<ModelsSummary> = {}): ModelsSummary {
	return {
		activeVersion: 1,
		canRollBack: false,
		candidates: [candidate()],
		...overrides
	};
}

describe('modelReadings', () => {
	it('sorts newest version first', () => {
		const readings = modelReadings(
			models({
				candidates: [
					candidate({ version: 1 }),
					candidate({ version: 3 }),
					candidate({ version: 2 })
				]
			})
		);

		expect(readings.map((reading) => reading.version)).toEqual([3, 2, 1]);
	});

	it('marks the active version', () => {
		const readings = modelReadings(
			models({
				activeVersion: 2,
				candidates: [candidate({ version: 1 }), candidate({ version: 2 })]
			})
		);

		const active = readings.find((reading) => reading.version === 2);
		const inactive = readings.find((reading) => reading.version === 1);
		expect(active?.active).toBe(true);
		expect(inactive?.active).toBe(false);
	});

	it('compares a non-active candidate against the active models own accuracy', () => {
		const readings = modelReadings(
			models({
				activeVersion: 1,
				candidates: [
					candidate({ version: 1, accuracyByClass: { 3: 0.8 } }),
					candidate({ version: 2, accuracyByClass: { 3: 0.95 } })
				]
			})
		);

		const [newest] = readings; // version 2, sorted first
		expect(newest.version).toBe(2);
		expect(newest.accuracyComparison).toEqual([
			{ slot: 3, candidateAccuracy: 0.95, activeAccuracy: 0.8 }
		]);
	});

	it('reports null for a class the active model excluded or the candidate excluded', () => {
		const readings = modelReadings(
			models({
				activeVersion: 1,
				candidates: [
					candidate({ version: 1, accuracyByClass: { 3: 0.8 } }),
					candidate({ version: 2, accuracyByClass: { 5: 0.7 } })
				]
			})
		);

		const newest = readings.find((reading) => reading.version === 2);
		expect(newest?.accuracyComparison).toEqual(
			expect.arrayContaining([
				{ slot: 3, candidateAccuracy: null, activeAccuracy: 0.8 },
				{ slot: 5, candidateAccuracy: 0.7, activeAccuracy: null }
			])
		);
	});

	it('has an empty comparison when nothing is active yet', () => {
		const readings = modelReadings(models({ activeVersion: null, candidates: [candidate()] }));

		expect(readings[0].accuracyComparison).toEqual([
			{ slot: 3, candidateAccuracy: 1.0, activeAccuracy: null },
			{ slot: 5, candidateAccuracy: 0.9, activeAccuracy: null }
		]);
		expect(readings[0].active).toBe(false);
	});
});

describe('suggestionAccuracyDetail', () => {
	it('says nothing has been matched yet when total is zero', () => {
		const detail = suggestionAccuracyDetail({ total: 0, correct: 0, accuracy: null });

		expect(detail).toContain('No suggestion has been matched');
	});

	it('reports the percentage and the raw counts', () => {
		const detail = suggestionAccuracyDetail({ total: 8, correct: 6, accuracy: 0.75 });

		expect(detail).toContain('75%');
		expect(detail).toContain('6 of 8 sorts');
	});

	it('uses the singular for exactly one matched sort', () => {
		const detail = suggestionAccuracyDetail({ total: 1, correct: 1, accuracy: 1.0 });

		expect(detail).toContain('1 of 1 sort ');
	});
});

function autonomySummary(overrides: Partial<AutonomySummary> = {}): AutonomySummary {
	return {
		thresholds: {},
		accuracyByClass: {},
		pendingReview: [],
		...overrides
	};
}

describe('autonomyClassReadings', () => {
	it('reports no threshold configured for a class with neither a threshold nor a review', () => {
		const readings = autonomyClassReadings(autonomySummary());

		expect(readings).toEqual([]);
	});

	it('reports a configured class with no attempts reviewed yet, distinct from a low false rate', () => {
		const readings = autonomyClassReadings(autonomySummary({ thresholds: { 3: 0.95 } }));

		expect(readings).toHaveLength(1);
		expect(readings[0].slot).toBe(3);
		expect(readings[0].threshold).toBe(0.95);
		expect(readings[0].detail).toContain('No autonomous attempt has been reviewed yet');
	});

	it('reports the false rate and raw counts for a reviewed class', () => {
		const readings = autonomyClassReadings(
			autonomySummary({
				thresholds: { 3: 0.95 },
				accuracyByClass: { 3: { total: 4, correct: 3, falseRate: 0.25 } }
			})
		);

		expect(readings[0].detail).toContain('25%');
		expect(readings[0].detail).toContain('1 of 4');
		expect(readings[0].tone).toBe('attention');
	});

	it('marks a clean reviewed class ordinary, not attention', () => {
		const readings = autonomyClassReadings(
			autonomySummary({
				thresholds: { 3: 0.95 },
				accuracyByClass: { 3: { total: 4, correct: 4, falseRate: 0 } }
			})
		);

		expect(readings[0].tone).toBe('ordinary');
	});

	it('includes a reviewed class even without a configured threshold', () => {
		const readings = autonomyClassReadings(
			autonomySummary({ accuracyByClass: { 5: { total: 2, correct: 2, falseRate: 0 } } })
		);

		expect(readings).toHaveLength(1);
		expect(readings[0].slot).toBe(5);
		expect(readings[0].threshold).toBeNull();
	});

	it('orders classes by slot', () => {
		const readings = autonomyClassReadings(autonomySummary({ thresholds: { 5: 0.9, 3: 0.95 } }));

		expect(readings.map((reading) => reading.slot)).toEqual([3, 5]);
	});
});

describe('pendingReviewDetail', () => {
	it('names the slot and when the attempt happened', () => {
		const detail = pendingReviewDetail(3, '2026-08-12T12:07:00.000Z');

		expect(detail).toContain('Slot 3');
		expect(detail).toContain('2026-08-12T12:07:00.000Z');
	});
});

import { describe, expect, it } from 'vitest';

import { classReadings, modelReadings, trainingReadinessDetail } from './dataset-view';
import type { CandidateSummary, DatasetSummary, ModelsSummary } from './dataset';

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

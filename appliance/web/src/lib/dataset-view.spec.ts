import { describe, expect, it } from 'vitest';

import { classReadings, trainingReadinessDetail } from './dataset-view';
import type { DatasetSummary } from './dataset';

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

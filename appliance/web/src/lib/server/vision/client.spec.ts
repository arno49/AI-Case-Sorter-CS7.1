/**
 * The dataset read, against a `cs71-vision` stand-in that is really listening.
 *
 * Reuses `daemon/harness.ts#startFakeDaemon`: it is a generic stand-in on a
 * real Unix domain socket, nothing specific to `cs71d`, and this is the same
 * shape a real `cs71-vision` answers with a Unix socket, a bearer credential
 * and a JSON body.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { replying, SERVICE_TOKEN, startFakeDaemon, type FakeDaemon } from '../daemon/harness';
import { InvalidCommandError, VisionClient } from './client';
import { VisionError } from './errors';

let vision: FakeDaemon;
let client: VisionClient;

function dataset(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		api_version: 'v1',
		minimum_examples_per_class: 40,
		classes: [
			{ slot: 3, count: 52, eligible: true },
			{ slot: 5, count: 12, eligible: false }
		],
		training_ready: true,
		...overrides
	};
}

async function raised(call: () => Promise<unknown>): Promise<VisionError> {
	try {
		await call();
	} catch (error) {
		return error as VisionError;
	}
	throw new Error('the call was expected to fail');
}

beforeEach(async () => {
	vision = await startFakeDaemon();
	client = new VisionClient({
		socketPath: vision.socketPath,
		serviceTokenPath: vision.serviceTokenPath
	});
});

afterEach(async () => {
	await vision.close();
});

describe('reading the dataset summary', () => {
	it('parses per-class counts, the floor and readiness', async () => {
		vision.answerWith(replying(200, dataset()));

		const summary = await client.datasetSummary();

		expect(summary).toEqual({
			minimumExamplesPerClass: 40,
			classes: [
				{ slot: 3, count: 52, eligible: true },
				{ slot: 5, count: 12, eligible: false }
			],
			trainingReady: true
		});
	});

	it('presents the shared service credential read from its protected file', async () => {
		vision.answerWith(replying(200, dataset()));

		await client.datasetSummary();

		expect(vision.lastRequest().headers.authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
	});

	it('requests the one resource this client knows, and nothing else', async () => {
		vision.answerWith(replying(200, dataset()));

		await client.datasetSummary();

		expect(vision.lastRequest().path).toBe('/v1/dataset');
	});

	it('handles no examples recorded yet as an empty list, not an error', async () => {
		vision.answerWith(replying(200, dataset({ classes: [], training_ready: false })));

		const summary = await client.datasetSummary();

		expect(summary.classes).toEqual([]);
		expect(summary.trainingReady).toBe(false);
	});
});

function models(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		api_version: 'v1',
		active_version: 2,
		can_roll_back: true,
		candidates: [
			{
				version: 1,
				trained_at: '2026-08-12T12:00:00.000Z',
				included_classes: [3, 5],
				excluded_classes: [7],
				accuracy_by_class: { '3': 1.0, '5': 0.9 },
				minimum_examples_per_class: 40,
				training_example_count: 64,
				holdout_example_count: 16
			}
		],
		...overrides
	};
}

describe('reading the models summary', () => {
	it('parses the active version and every candidate', async () => {
		vision.answerWith(replying(200, models()));

		const summary = await client.models();

		expect(summary.activeVersion).toBe(2);
		expect(summary.candidates).toEqual([
			{
				version: 1,
				trainedAt: '2026-08-12T12:00:00.000Z',
				includedClasses: [3, 5],
				excludedClasses: [7],
				accuracyByClass: { 3: 1.0, 5: 0.9 },
				minimumExamplesPerClass: 40,
				trainingExampleCount: 64,
				holdoutExampleCount: 16
			}
		]);
	});

	it('requests /v1/models', async () => {
		vision.answerWith(replying(200, models()));

		await client.models();

		expect(vision.lastRequest().path).toBe('/v1/models');
	});

	it('reports no active version as null, not a made-up zero', async () => {
		vision.answerWith(
			replying(200, models({ active_version: null, can_roll_back: false, candidates: [] }))
		);

		const summary = await client.models();

		expect(summary.activeVersion).toBeNull();
	});

	it('parses whether a rollback has anything to go back to', async () => {
		vision.answerWith(replying(200, models({ can_roll_back: false })));

		const summary = await client.models();

		expect(summary.canRollBack).toBe(false);
	});

	it('is malformed when can_roll_back is missing', async () => {
		const withoutFlag = models();
		delete withoutFlag.can_roll_back;
		vision.answerWith(replying(200, withoutFlag));

		const error = await raised(() => client.models());

		expect(error.kind).toBe('malformed');
	});

	it('is malformed when a candidate entry is missing a usable field', async () => {
		vision.answerWith(
			replying(
				200,
				models({
					candidates: [{ version: 1, trained_at: 'x', included_classes: [], excluded_classes: [] }]
				})
			)
		);

		const error = await raised(() => client.models());

		expect(error.kind).toBe('malformed');
	});
});

describe('triggering training', () => {
	it('reports a started run', async () => {
		vision.answerWith(replying(200, { api_version: 'v1', started: true }));

		const result = await client.train();

		expect(result.started).toBe(true);
	});

	it('reports a run already in flight, not an error', async () => {
		vision.answerWith(replying(200, { api_version: 'v1', started: false }));

		const result = await client.train();

		expect(result.started).toBe(false);
	});

	it('is a POST to /v1/train', async () => {
		vision.answerWith(replying(200, { api_version: 'v1', started: true }));

		await client.train();

		expect(vision.lastRequest().method).toBe('POST');
		expect(vision.lastRequest().path).toBe('/v1/train');
	});
});

describe('activating a candidate', () => {
	it('is a POST to the version-scoped resource', async () => {
		vision.answerWith(
			replying(200, {
				api_version: 'v1',
				active_version: 3,
				activated_at: '2026-08-12T12:00:00.000Z'
			})
		);

		const result = await client.activate(3);

		expect(vision.lastRequest().method).toBe('POST');
		expect(vision.lastRequest().path).toBe('/v1/models/3/activate');
		expect(result).toEqual({ activeVersion: 3, activatedAt: '2026-08-12T12:00:00.000Z' });
	});

	it('rejects a non-positive version before anything is sent', async () => {
		await expect(client.activate(0)).rejects.toBeInstanceOf(InvalidCommandError);
		await expect(client.activate(-1)).rejects.toBeInstanceOf(InvalidCommandError);
		await expect(client.activate(1.5)).rejects.toBeInstanceOf(InvalidCommandError);
		expect(vision.requests).toEqual([]);
	});

	it('is rejected when cs71-vision does not know that version', async () => {
		vision.answerWith(replying(404, { code: 'RESOURCE_NOT_FOUND', message: 'no' }));

		const error = await raised(() => client.activate(999));

		expect(error.kind).toBe('rejected');
		expect(error.code).toBe('RESOURCE_NOT_FOUND');
		expect(error.safeResponse.status).toBe(404);
		expect(error.safeResponse.message).toBe('That model version is not on record.');
	});
});

describe('rolling back', () => {
	it('is a POST to /v1/rollback', async () => {
		vision.answerWith(
			replying(200, {
				api_version: 'v1',
				active_version: 1,
				activated_at: '2026-08-12T12:00:00.000Z'
			})
		);

		const result = await client.rollback();

		expect(vision.lastRequest().method).toBe('POST');
		expect(vision.lastRequest().path).toBe('/v1/rollback');
		expect(result).toEqual({ activeVersion: 1, activatedAt: '2026-08-12T12:00:00.000Z' });
	});

	it('is rejected with a clear reason when there is nothing to roll back to', async () => {
		vision.answerWith(
			replying(400, { code: 'VALIDATION_FAILED', message: 'there is no previous version' })
		);

		const error = await raised(() => client.rollback());

		expect(error.safeResponse.status).toBe(400);
		expect(error.safeResponse.message).toBe('There is no previous version to roll back to.');
	});
});

describe('what goes wrong', () => {
	it('is unreachable when nothing is listening', async () => {
		await vision.close();

		const error = await raised(() => client.datasetSummary());

		expect(error).toBeInstanceOf(VisionError);
		expect(error.kind).toBe('unreachable');
	});

	it('is rejected when cs71-vision refuses the credential', async () => {
		vision.answerWith(replying(401, { code: 'UNAUTHENTICATED', message: 'no' }));

		const error = await raised(() => client.datasetSummary());

		expect(error.kind).toBe('rejected');
		expect(error.status).toBe(401);
		expect(error.code).toBe('UNAUTHENTICATED');
	});

	it('is malformed when the body is missing the expected shape', async () => {
		vision.answerWith(replying(200, { api_version: 'v1' }));

		const error = await raised(() => client.datasetSummary());

		expect(error.kind).toBe('malformed');
	});

	it('is malformed when a class entry is missing a usable field', async () => {
		vision.answerWith(
			replying(200, dataset({ classes: [{ slot: 3, count: 'not-a-number', eligible: true }] }))
		);

		const error = await raised(() => client.datasetSummary());

		expect(error.kind).toBe('malformed');
	});

	it('never puts cs71-visions own words in the safe response', async () => {
		vision.answerWith(replying(401, { code: 'UNAUTHENTICATED', message: 'no' }));

		const error = await raised(() => client.datasetSummary());

		expect(error.safeResponse.message).not.toContain('UNAUTHENTICATED');
		expect(error.safeResponse.message).toBe('The classifier service is not answering.');
	});
});

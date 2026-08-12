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
import { VisionClient } from './client';
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

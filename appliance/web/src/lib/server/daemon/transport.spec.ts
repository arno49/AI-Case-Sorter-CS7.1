/**
 * One exchange with a daemon that is really listening on a socket.
 */

import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DaemonError } from './errors';
import { startFakeDaemon, replying, type FakeDaemon } from './harness';
import { MAXIMUM_RESPONSE_BYTES, exchange } from './transport';

let daemon: FakeDaemon;

async function raised(call: () => Promise<unknown>): Promise<DaemonError> {
	try {
		await call();
	} catch (error) {
		return error as DaemonError;
	}
	throw new Error('the call was expected to fail');
}

beforeEach(async () => {
	daemon = await startFakeDaemon();
});

afterEach(async () => {
	await daemon.close();
});

describe('a completed exchange', () => {
	it('carries the method, path, headers and body to the daemon', async () => {
		daemon.answerWith(replying(202, { api_version: 'v1' }));

		await exchange(daemon.socketPath, {
			method: 'POST',
			path: '/v1/machine/stop',
			headers: { authorization: 'Bearer a-credential' },
			body: '{"api_version":"v1"}',
			timeoutMs: 2_000
		});

		expect(daemon.lastRequest()).toMatchObject({
			method: 'POST',
			path: '/v1/machine/stop',
			body: '{"api_version":"v1"}'
		});
		expect(daemon.lastRequest().headers.authorization).toBe('Bearer a-credential');
	});

	it('declares the length and type of what it sends', async () => {
		daemon.answerWith(replying(202, {}));

		await exchange(daemon.socketPath, {
			method: 'POST',
			path: '/v1/machine/stop',
			headers: {},
			body: '{"a":1}',
			timeoutMs: 2_000
		});

		expect(daemon.lastRequest().headers['content-type']).toBe('application/json');
		expect(daemon.lastRequest().headers['content-length']).toBe('7');
	});

	it('returns the status and the parsed body', async () => {
		daemon.answerWith(replying(200, { api_version: 'v1', generation: 3 }));

		expect(await exchange(daemon.socketPath, read('/v1/snapshot'))).toEqual({
			status: 200,
			body: { api_version: 'v1', generation: 3 }
		});
	});

	it('returns a refusal rather than throwing, so the caller can map it', async () => {
		daemon.answerWith(replying(409, { api_version: 'v1', code: 'STALE_GENERATION' }));

		expect((await exchange(daemon.socketPath, read('/v1/snapshot'))).status).toBe(409);
	});

	it('treats an empty body as no body', async () => {
		daemon.answerWith((_request, response) => {
			response.writeHead(200);
			response.end();
		});

		expect(await exchange(daemon.socketPath, read('/v1/snapshot'))).toEqual({
			status: 200,
			body: undefined
		});
	});
});

describe('an answer that cannot be used', () => {
	it('reports a body that is not JSON as malformed', async () => {
		daemon.answerWith((_request, response) => {
			response.writeHead(200, { 'content-type': 'application/json' });
			response.end('not json at all');
		});

		expect((await raised(() => exchange(daemon.socketPath, read('/v1/snapshot')))).kind).toBe(
			'malformed'
		);
	});

	it('gives up on a body larger than this service will hold', async () => {
		daemon.answerWith((_request, response) => {
			response.writeHead(200, { 'content-type': 'application/json' });
			response.end(`"${'x'.repeat(MAXIMUM_RESPONSE_BYTES + 1)}"`);
		});

		expect((await raised(() => exchange(daemon.socketPath, read('/v1/snapshot')))).kind).toBe(
			'malformed'
		);
	});
});

describe('a daemon that does not answer', () => {
	it('reports an absent socket as unreachable', async () => {
		const absent = join(daemon.directory, 'not-a-socket');

		expect((await raised(() => exchange(absent, read('/v1/snapshot')))).kind).toBe('unreachable');
	});

	it('gives up on a silent daemon and calls it a timeout, not a failure', async () => {
		// The request was sent. Whether the machine acted on it is unknown, which
		// is a different answer from "it did not happen".
		daemon.answerWith(() => {});

		const error = await raised(() =>
			exchange(daemon.socketPath, { ...read('/v1/snapshot'), timeoutMs: 50 })
		);

		expect(error.kind).toBe('timeout');
		expect(error.safeResponse.status).toBe(504);
	});
});

describe('the paths this transport will dial', () => {
	it('refuses anything outside the contract namespace', async () => {
		await expect(exchange(daemon.socketPath, read('/admin'))).rejects.toBeInstanceOf(DaemonError);
	});

	it('refuses a path that tries to climb out of it', async () => {
		await expect(
			exchange(daemon.socketPath, read('/v1/operations/../../admin'))
		).rejects.toBeInstanceOf(DaemonError);
	});

	it('allows a query string, which the operations list needs', async () => {
		daemon.answerWith(replying(200, { api_version: 'v1' }));

		expect((await exchange(daemon.socketPath, read('/v1/operations?limit=20'))).status).toBe(200);
	});
});

function read(path: string) {
	return { method: 'GET' as const, path, headers: {}, timeoutMs: 2_000 };
}

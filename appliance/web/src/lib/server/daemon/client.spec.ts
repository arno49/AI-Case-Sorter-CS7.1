/**
 * The commands, against a daemon that is really listening.
 *
 * These assert the two things a browser must never be able to influence — the
 * path and the credential — and the three headers the contract requires, which
 * are what keep a resubmitted form from moving the machine twice.
 */

import { rmSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
	DEADLINES,
	DaemonClient,
	InvalidCommandError,
	actorFor,
	newIdempotencyKey,
	type Actor
} from './client';
import { DaemonError } from './errors';
import {
	SERVICE_TOKEN,
	acceptedOperation,
	replying,
	startFakeDaemon,
	type FakeDaemon
} from './harness';

let daemon: FakeDaemon;
let client: DaemonClient;

const ACTOR: Actor = { user_id: 'user_0123456789abcdef', role: 'operator' };

function sent(): { headers: Record<string, string>; body: Record<string, unknown>; path: string } {
	const request = daemon.lastRequest();
	return {
		headers: request.headers,
		body: request.body === '' ? {} : JSON.parse(request.body),
		path: request.path
	};
}

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
	client = new DaemonClient({
		socketPath: daemon.socketPath,
		serviceTokenPath: daemon.serviceTokenPath
	});
	daemon.answerWith(replying(202, acceptedOperation()));
});

afterEach(async () => {
	await daemon.close();
});

describe('what every command carries', () => {
	it('presents the service credential read from its protected file', async () => {
		await client.stop({ actor: ACTOR });

		expect(sent().headers.authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
	});

	it('reads that file once rather than on every command', async () => {
		await client.stop({ actor: ACTOR });
		// A credential put through the filesystem on every command would be read
		// far more often than any rotation needs; rotation is a service restart.
		rmSync(daemon.serviceTokenPath);

		await client.stop({ actor: ACTOR });

		expect(daemon.requests[1].headers.authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
	});

	it('supplies an idempotency key in the alphabet the contract allows', async () => {
		await client.home({ actor: ACTOR, generation: 4 }, 'both');

		expect(sent().headers['idempotency-key']).toMatch(/^[A-Za-z0-9._~-]{16,128}$/);
	});

	it('uses the caller key when one is given, so a resubmission is one command', async () => {
		const key = newIdempotencyKey();

		await client.sort({ actor: ACTOR, generation: 4, idempotencyKey: key }, 3);
		await client.sort({ actor: ACTOR, generation: 4, idempotencyKey: key }, 3);

		expect(daemon.requests.map((request) => request.headers['idempotency-key'])).toEqual([
			key,
			key
		]);
	});

	it('generates a different key for each command when none is given', async () => {
		await client.sort({ actor: ACTOR, generation: 4 }, 3);
		await client.sort({ actor: ACTOR, generation: 4 }, 3);

		const [first, second] = daemon.requests.map((r) => r.headers['idempotency-key']);
		expect(first).not.toBe(second);
	});

	it('matches the generation the operator was looking at', async () => {
		await client.sort({ actor: ACTOR, generation: 11 }, 3);

		expect(sent().headers['if-match-generation']).toBe('11');
	});

	it('gives the daemon the deadline for that kind of work', async () => {
		await client.home({ actor: ACTOR, generation: 1 }, 'feeder');

		expect(sent().headers['x-deadline-ms']).toBe(String(DEADLINES.home));
	});

	it('attributes the command to the signed-in account without a browser credential', async () => {
		await client.feed({ actor: ACTOR, generation: 2 });

		expect(sent().body).toEqual({
			api_version: 'v1',
			actor: { user_id: 'user_0123456789abcdef', role: 'operator' }
		});
	});
});

describe('the software stop', () => {
	it('matches any generation, so a page a few seconds old can still stop the machine', async () => {
		await client.stop({ actor: ACTOR });

		expect(sent().headers['if-match-generation']).toBe('*');
		expect(sent().path).toBe('/v1/machine/stop');
	});

	it('still matches a generation when the caller insists on one', async () => {
		await client.stop({ actor: ACTOR, generation: 9 });

		expect(sent().headers['if-match-generation']).toBe('9');
	});
});

describe('the operation history', () => {
	beforeEach(() => {
		daemon.answerWith(replying(200, { api_version: 'v1', items: [], next_cursor: null }));
	});

	it('reads with no query string when the caller asked for nothing in particular', async () => {
		await client.operations();

		expect(sent().path).toBe('/v1/operations');
	});

	it('carries a state, a type, a limit and a cursor the caller already validated', async () => {
		await client.operations({ state: 'FAILED', type: 'SORT', limit: 10, cursor: 'a-cursor' });

		const params = new URLSearchParams(sent().path.split('?')[1]);
		expect(params.get('state')).toBe('FAILED');
		expect(params.get('type')).toBe('SORT');
		expect(params.get('limit')).toBe('10');
		expect(params.get('cursor')).toBe('a-cursor');
	});

	it('uses the read deadline, not a command deadline', async () => {
		await client.operations();

		expect(sent().headers['x-deadline-ms']).toBe(String(DEADLINES.read));
	});
});

describe('the system read', () => {
	beforeEach(() => {
		daemon.answerWith(
			replying(200, {
				api_version: 'v1',
				dtr_gate_status: 'NOT_EXECUTED',
				observed_at: '2026-08-11T12:00:00.000Z'
			})
		);
	});

	it('reads a fixed path with no session or generation of its own', async () => {
		await client.system();

		expect(sent().path).toBe('/v1/system');
	});

	it('uses the read deadline, not a command deadline', async () => {
		await client.system();

		expect(sent().headers['x-deadline-ms']).toBe(String(DEADLINES.read));
	});
});

describe('what the browser cannot choose', () => {
	it('has no method that takes a path, a device or a protocol string', () => {
		const surface = Object.getOwnPropertyNames(DaemonClient.prototype);

		expect(surface.sort()).toEqual([
			'configuration',
			'connect',
			'constructor',
			'feed',
			'home',
			'operation',
			'operations',
			'recover',
			'snapshot',
			'sort',
			'stop',
			'system',
			'updateConfiguration'
		]);
	});

	it('refuses an operation id that is not one', async () => {
		await expect(client.operation('../../admin')).rejects.toBeInstanceOf(InvalidCommandError);
	});

	it('refuses a slot outside the contract range before anything is sent', async () => {
		await expect(client.sort({ actor: ACTOR, generation: 1 }, 64)).rejects.toBeInstanceOf(
			InvalidCommandError
		);
		await expect(client.sort({ actor: ACTOR, generation: 1 }, 1.5)).rejects.toBeInstanceOf(
			InvalidCommandError
		);
		expect(daemon.requests).toEqual([]);
	});

	it('refuses a home target it does not recognise', async () => {
		await expect(
			client.home({ actor: ACTOR, generation: 1 }, 'everything' as never)
		).rejects.toBeInstanceOf(InvalidCommandError);
	});

	it('refuses a generation that is not one', async () => {
		await expect(client.feed({ actor: ACTOR, generation: -1 })).rejects.toBeInstanceOf(
			InvalidCommandError
		);
	});

	it('refuses an idempotency key the contract would not accept', async () => {
		await expect(
			client.feed({ actor: ACTOR, generation: 1, idempotencyKey: 'too short' })
		).rejects.toBeInstanceOf(InvalidCommandError);
	});
});

describe('recovery', () => {
	it('requires the confirmation explicitly, and sends it', async () => {
		await client.recover({ actor: ACTOR, generation: 5 }, true);

		expect(sent().body).toMatchObject({ confirm_uncertain_recovery: true });
		expect(sent().path).toBe('/v1/session/recover');
	});

	it('refuses to recover without it', async () => {
		await expect(
			client.recover({ actor: ACTOR, generation: 5 }, false as never)
		).rejects.toBeInstanceOf(InvalidCommandError);
	});
});

describe('an accepted command', () => {
	it('returns the operation identity and a pending state, not a completion', async () => {
		const accepted = await client.connect({ actor: ACTOR, generation: 0 });

		expect(accepted.operation_id).toBe('0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c');
		expect(accepted.state).toBe('ACCEPTED');
		expect(['QUEUED', 'ACCEPTED', 'RUNNING']).toContain(accepted.state);
	});

	it('is not believed when the daemon answers without an operation identity', async () => {
		daemon.answerWith(replying(202, acceptedOperation({ operation_id: 'not-a-uuid' })));

		expect((await raised(() => client.stop({ actor: ACTOR }))).kind).toBe('malformed');
	});

	it('is not believed when the answer is from another contract version', async () => {
		daemon.answerWith(replying(202, acceptedOperation({ api_version: 'v2' })));

		expect((await raised(() => client.stop({ actor: ACTOR }))).kind).toBe('malformed');
	});

	it('is not believed when the daemon answers 200 to a command', async () => {
		// Only 202 means accepted. A 200 would be a claim this contract does not
		// make about a command.
		daemon.answerWith(replying(200, acceptedOperation()));

		expect((await raised(() => client.stop({ actor: ACTOR }))).kind).toBe('rejected');
	});
});

describe('a refused command', () => {
	it('carries the daemon code and request id for correlation', async () => {
		daemon.answerWith(
			replying(409, {
				api_version: 'v1',
				code: 'STALE_GENERATION',
				message: 'generation 4 is stale',
				request_id: 'a5b1c2d3-0000-4000-8000-000000000000'
			})
		);

		const error = await raised(() => client.sort({ actor: ACTOR, generation: 4 }, 2));

		expect(error.code).toBe('STALE_GENERATION');
		expect(error.requestId).toBe('a5b1c2d3-0000-4000-8000-000000000000');
		expect(error.safeResponse.status).toBe(409);
	});

	it('says nothing to the browser about what the daemon said', async () => {
		daemon.answerWith(
			replying(503, {
				api_version: 'v1',
				code: 'SERVICE_UNAVAILABLE',
				message: '/dev/cs71 is not open',
				request_id: 'a5b1c2d3-0000-4000-8000-000000000000'
			})
		);

		const error = await raised(() => client.stop({ actor: ACTOR }));

		expect(error.safeResponse.message).not.toContain('/dev/cs71');
	});
});

describe('reading the machine', () => {
	it('asks for the snapshot and returns it', async () => {
		daemon.answerWith(replying(200, { api_version: 'v1', generation: 12, ready: false }));

		const snapshot = await client.snapshot();

		expect(sent().path).toBe('/v1/snapshot');
		expect(snapshot.generation).toBe(12);
	});

	it('sends the credential on a read as well', async () => {
		daemon.answerWith(replying(200, { api_version: 'v1' }));

		await client.configuration();

		expect(sent().headers.authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
	});

	it('refuses a snapshot that is not from this contract', async () => {
		daemon.answerWith(replying(200, { api_version: 'v2' }));

		expect((await raised(() => client.snapshot())).kind).toBe('malformed');
	});
});

describe('attribution', () => {
	it('is built from the signed-in account, and carries nothing else', () => {
		expect(
			actorFor({ userId: 'user_0123456789abcdef', role: 'administrator', username: 'ada' } as never)
		).toEqual({ user_id: 'user_0123456789abcdef', role: 'administrator' });
	});
});

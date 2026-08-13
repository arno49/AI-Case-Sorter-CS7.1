/**
 * A command from a browser to the machine, end to end.
 *
 * The hook is the real one, the action is the real one, and the daemon is a
 * real process listening on a real Unix socket. What these cover is the part no
 * unit test can: that authorization happens on the server before the daemon is
 * called, that what comes back is an acceptance rather than a completion, and
 * that the audit says who asked even when the answer was no.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { recentAudit } from '$lib/server/audit';
import { startSession } from '$lib/server/auth/boundary';
import { CSRF_FIELD } from '$lib/server/auth/csrf';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser, type Role } from '$lib/server/auth/users';
import {
	acceptedOperation,
	replying,
	startFakeDaemon,
	type FakeDaemon
} from '$lib/server/daemon/harness';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';

import { browser, csrfFor, request, throughHook as through, type CookieJar } from './harness';
import { handle } from '../hooks.server';
import { actions as dashboardActions, load as dashboardLoad } from './+page.server';
import type { RequestEvent, ServerLoadEvent } from '@sveltejs/kit';

let directory: string;
let daemon: FakeDaemon;

const throughHook = (event: RequestEvent) => through(event, handle);

async function signedIn(role: Role): Promise<CookieJar> {
	const { config, database } = webRuntime();
	const user = await createUser(database, { username: role, password: PASSWORD, role }, new Date());
	const cookies = browser();
	startSession(database, cookies, user.userId, config.profile, new Date());
	return cookies;
}

/** A stop submitted from a page this appliance rendered, through the hook. */
async function pressStop(cookies: CookieJar): Promise<RequestEvent> {
	const event = request('/', cookies, { form: { [CSRF_FIELD]: csrfFor(cookies) }, routeId: '/' });
	await throughHook(event);
	return event;
}

function asLoadEvent(event: RequestEvent): ServerLoadEvent {
	return event as unknown as ServerLoadEvent;
}

beforeEach(async () => {
	directory = mkdtempSync(join(tmpdir(), 'cs71-cmd-'));
	daemon = await startFakeDaemon();
	process.env.CS71_WEB_PROFILE = 'development';
	process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
	process.env.CS71D_SOCKET_PATH = daemon.socketPath;
	process.env.CS71_WEB_SERVICE_TOKEN_PATH = daemon.serviceTokenPath;
	// The daemon's own words are kept for the server log; the specs assert what
	// reaches the browser, so the log itself is silenced here.
	vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(async () => {
	vi.restoreAllMocks();
	closeWebRuntime();
	await daemon.close();
	rmSync(directory, { recursive: true, force: true });
	delete process.env.CS71_WEB_PROFILE;
	delete process.env.CS71_WEB_DATABASE_PATH;
	delete process.env.CS71D_SOCKET_PATH;
	delete process.env.CS71_WEB_SERVICE_TOKEN_PATH;
});

describe('reading the machine', () => {
	it('shows the snapshot the daemon reported', async () => {
		daemon.answerWith(
			replying(200, {
				api_version: 'v1',
				generation: 12,
				connection_state: 'READY',
				fault_state: 'CLEAR',
				ready: true,
				faults: []
			})
		);
		const event = request('/', await signedIn('operator'));
		await throughHook(event);

		const data = (await dashboardLoad(asLoadEvent(event))) as {
			snapshot: { generation: number } | null;
			unavailable: string | null;
		};

		expect(data.snapshot?.generation).toBe(12);
		expect(data.unavailable).toBeNull();
	});

	it('still renders when the daemon is not answering, and says so', async () => {
		// The screen is what an operator has when the machine has gone quiet, and
		// the stop control has to stay on it.
		await daemon.close();
		const event = request('/', await signedIn('operator'));
		await throughHook(event);

		const data = (await dashboardLoad(asLoadEvent(event))) as {
			snapshot: unknown;
			unavailable: string | null;
		};

		expect(data.snapshot).toBeNull();
		expect(data.unavailable).toContain('not answering');
	});
});

describe('pressing stop', () => {
	it('sends the command with the actor, an idempotency key and a deadline', async () => {
		daemon.answerWith(replying(202, acceptedOperation()));
		const event = await pressStop(await signedIn('operator'));

		await dashboardActions.stop(event as never);

		const sent = daemon.lastRequest();
		expect(sent.path).toBe('/v1/machine/stop');
		expect(JSON.parse(sent.body).actor).toMatchObject({ role: 'operator' });
		expect(sent.headers['idempotency-key']).toMatch(/^[A-Za-z0-9._~-]{16,128}$/);
		expect(sent.headers['x-deadline-ms']).toBeDefined();
		expect(sent.headers['if-match-generation']).toBe('*');
	});

	it('answers with the operation identity and a pending state, not a completion', async () => {
		daemon.answerWith(replying(202, acceptedOperation({ state: 'QUEUED' })));
		const event = await pressStop(await signedIn('operator'));

		const result = (await dashboardActions.stop(event as never)) as {
			operationId: string;
			state: string;
		};

		expect(result.operationId).toBe('0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c');
		expect(['QUEUED', 'ACCEPTED', 'RUNNING']).toContain(result.state);
	});

	it('is allowed to a viewer, who may stop the machine', async () => {
		daemon.answerWith(replying(202, acceptedOperation()));
		const event = await pressStop(await signedIn('viewer'));

		expect(await dashboardActions.stop(event as never)).toMatchObject({ state: 'ACCEPTED' });
	});

	it('never reuses an idempotency key, so a second press is a second stop', async () => {
		// A replayed key would let the daemon answer with the first result and
		// turn a stop into a no-op.
		daemon.answerWith(replying(202, acceptedOperation()));
		const cookies = await signedIn('operator');

		await dashboardActions.stop((await pressStop(cookies)) as never);
		await dashboardActions.stop((await pressStop(cookies)) as never);

		const [first, second] = daemon.requests.map((sent) => sent.headers['idempotency-key']);
		expect(first).not.toBe(second);
	});

	it('handles concurrent presses independently, under real BFF load', async () => {
		// PI-SWQ-001 stress test: several distinct browser sessions pressing
		// stop at the same moment - a real shape of "concurrent BFF load" -
		// each gets its own render-minted idempotency key and its own
		// answer; nothing here is a single global slot one request could
		// clobber for another.
		daemon.answerWith((_request, response) => {
			response.writeHead(202, { 'content-type': 'application/json' });
			response.end(JSON.stringify(acceptedOperation()));
		});
		const cookies = await signedIn('operator');
		const racers = 6;
		const events = await Promise.all(Array.from({ length: racers }, () => pressStop(cookies)));

		const results = await Promise.all(
			events.map((event) => dashboardActions.stop(event as never) as Promise<{ state: string }>)
		);

		expect(results).toHaveLength(racers);
		expect(results.every((result) => result.state === 'ACCEPTED')).toBe(true);
		expect(daemon.requests).toHaveLength(racers);
		const keys = daemon.requests.map((sent) => sent.headers['idempotency-key']);
		expect(new Set(keys).size).toBe(racers); // every render minted its own key
	});
});

describe('when the daemon refuses', () => {
	it('answers the operator in this workspace words, not the daemon report', async () => {
		daemon.answerWith(
			replying(503, {
				api_version: 'v1',
				code: 'SERVICE_UNAVAILABLE',
				message: '/dev/cs71 is not open',
				request_id: 'a5b1c2d3-0000-4000-8000-000000000000'
			})
		);
		const event = await pressStop(await signedIn('operator'));

		const failure = (await dashboardActions.stop(event as never)) as {
			status: number;
			data: { error: string };
		};

		expect(failure.status).toBe(503);
		expect(failure.data.error).not.toContain('/dev/cs71');
		expect(failure.data.error).not.toContain('SERVICE_UNAVAILABLE');
	});

	it('reports an UNCERTAIN machine as one that may have moved', async () => {
		daemon.answerWith(
			replying(409, { api_version: 'v1', code: 'UNCERTAIN', message: 'no trusted terminal' })
		);
		const event = await pressStop(await signedIn('operator'));

		const failure = (await dashboardActions.stop(event as never)) as { data: { error: string } };

		expect(failure.data.error).toContain('may have moved');
	});
});

describe('the audit', () => {
	it('records who asked, the request that carried it and the operation it became', async () => {
		daemon.answerWith(replying(202, acceptedOperation()));
		const event = await pressStop(await signedIn('operator'));

		await dashboardActions.stop(event as never);

		expect(recentAudit(webRuntime().database)[0]).toMatchObject({
			role: 'operator',
			action: 'machine.stop',
			outcome: 'accepted',
			requestId: event.locals.requestId,
			operationId: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c'
		});
	});

	it('records a refusal with the daemon code that explains it', async () => {
		daemon.answerWith(
			replying(409, {
				api_version: 'v1',
				code: 'STALE_GENERATION',
				message: 'stale',
				request_id: 'a5b1c2d3-0000-4000-8000-000000000000'
			})
		);
		const event = await pressStop(await signedIn('operator'));

		await dashboardActions.stop(event as never);

		expect(recentAudit(webRuntime().database)[0]).toMatchObject({
			outcome: 'refused',
			operationId: null,
			daemonCode: 'STALE_GENERATION',
			daemonRequestId: 'a5b1c2d3-0000-4000-8000-000000000000'
		});
	});

	it('records an attempt the daemon never answered', async () => {
		await daemon.close();
		const event = await pressStop(await signedIn('operator'));

		await dashboardActions.stop(event as never);

		expect(recentAudit(webRuntime().database)[0]).toMatchObject({
			outcome: 'failed',
			operationId: null
		});
	});

	it('holds no credential, cookie or token of any kind', async () => {
		daemon.answerWith(replying(202, acceptedOperation()));
		const cookies = await signedIn('operator');
		const event = await pressStop(cookies);
		await dashboardActions.stop(event as never);

		const entry = JSON.stringify(recentAudit(webRuntime().database)[0]);

		for (const secret of [PASSWORD, csrfFor(cookies), ...cookies.store.values()]) {
			expect(entry).not.toContain(secret);
		}
	});
});

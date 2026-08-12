/**
 * The machine's events, from the daemon's socket to a browser's `EventSource`.
 *
 * The hook is the real one, the route is the real one, and the daemon is a real
 * process streaming real SSE frames over a real Unix socket. What that covers is
 * the part a unit test cannot: that a browser is authorized before it is
 * attached, that the bytes on the wire carry the daemon's own cursor, and that
 * restarting this service leaves the daemon's operation exactly where it was.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { startSession } from '$lib/server/auth/boundary';
import { CSRF_FIELD } from '$lib/server/auth/csrf';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser, type Role } from '$lib/server/auth/users';
import {
	acceptedOperation,
	daemonEvent,
	eventStream,
	replying,
	startFakeDaemon,
	type FakeDaemon,
	type FakeEventStream
} from '$lib/server/daemon/harness';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';

import { browser, csrfFor, request, throughHook as through, type CookieJar } from './harness';
import { handle } from '../hooks.server';
import { actions as dashboardActions } from './+page.server';
import { GET } from './events/+server';
import type { RequestEvent } from '@sveltejs/kit';

let directory: string;
let daemon: FakeDaemon;
let stream: FakeEventStream;

const throughHook = (event: RequestEvent) => through(event, handle);

async function signedIn(role: Role): Promise<CookieJar> {
	const { config, database } = webRuntime();
	const user = await createUser(database, { username: role, password: PASSWORD, role }, new Date());
	const cookies = browser();
	startSession(database, cookies, user.userId, config.profile, new Date());
	return cookies;
}

/** One SSE frame, as the browser's parser would see it. */
interface Frame {
	readonly id?: string;
	readonly name?: string;
	readonly data: string;
}

interface Watching {
	next: () => Promise<Frame>;
	take: (count: number) => Promise<Frame[]>;
	leave: () => Promise<void>;
}

/**
 * Attach a browser and read the frames it receives.
 *
 * Comment frames are skipped the way a browser skips them: they keep a proxy
 * from closing an idle connection and are not events.
 */
async function watch(cookies: CookieJar, lastEventId?: string): Promise<Watching> {
	const event = request('/events', cookies, {
		routeId: '/events',
		headers: lastEventId === undefined ? {} : { 'last-event-id': lastEventId }
	});
	await throughHook(event);
	const response = await GET(event as never);
	expect(response.headers.get('content-type')).toBe('text/event-stream');
	const reader = response.body!.getReader();
	const decoder = new TextDecoder();
	let buffer = '';

	const next = async (): Promise<Frame> => {
		for (;;) {
			const boundary = buffer.indexOf('\n\n');
			if (boundary !== -1) {
				const raw = buffer.slice(0, boundary);
				buffer = buffer.slice(boundary + 2);
				if (!raw.startsWith(':')) {
					return parseFrame(raw);
				}
				continue;
			}
			const { value, done } = await reader.read();
			if (done) {
				throw new Error('the stream ended');
			}
			buffer += decoder.decode(value, { stream: true });
		}
	};

	return {
		next,
		take: async (count) => {
			const seen: Frame[] = [];
			while (seen.length < count) {
				seen.push(await next());
			}
			return seen;
		},
		leave: async () => {
			await reader.cancel();
		}
	};
}

/**
 * Attach a browser and wait until this process is reading the daemon.
 *
 * Only the browser that causes the attachment can wait for it: the point of the
 * hub is that the next one joins a reader that is already open. A frame sent
 * before the reader is attached would go nowhere, and a spec that raced that
 * would hang rather than fail.
 */
async function watchAttached(cookies: CookieJar, lastEventId?: string): Promise<Watching> {
	const connected = stream.nextConnection();
	const watcher = await watch(cookies, lastEventId);
	await connected;
	return watcher;
}

function parseFrame(raw: string): Frame {
	const lines = raw.split('\n');
	const field = (name: string) =>
		lines
			.filter((line) => line.startsWith(`${name}:`))
			.map((line) => line.slice(name.length + 1).trimStart());
	return {
		id: field('id')[0],
		name: field('event')[0],
		data: field('data').join('\n')
	};
}

/** A stop pressed from a page this appliance rendered, through the hook. */
async function pressStop(cookies: CookieJar): Promise<void> {
	const event = request('/', cookies, { form: { [CSRF_FIELD]: csrfFor(cookies) }, routeId: '/' });
	await throughHook(event);
	await dashboardActions.stop(event as never);
}

beforeEach(async () => {
	directory = mkdtempSync(join(tmpdir(), 'cs71-sse-'));
	daemon = await startFakeDaemon();
	stream = eventStream();
	daemon.answerWith((incoming, response) => {
		if (incoming.path === '/v1/events') {
			stream.handler(incoming, response);
			return;
		}
		replying(202, acceptedOperation({ state: 'RUNNING' }))(incoming, response);
	});
	process.env.CS71_WEB_PROFILE = 'development';
	process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
	process.env.CS71D_SOCKET_PATH = daemon.socketPath;
	process.env.CS71_WEB_SERVICE_TOKEN_PATH = daemon.serviceTokenPath;
	vi.spyOn(console, 'error').mockImplementation(() => {});
});

afterEach(async () => {
	vi.restoreAllMocks();
	stream.end();
	closeWebRuntime();
	await daemon.close();
	rmSync(directory, { recursive: true, force: true });
	delete process.env.CS71_WEB_PROFILE;
	delete process.env.CS71_WEB_DATABASE_PATH;
	delete process.env.CS71D_SOCKET_PATH;
	delete process.env.CS71_WEB_SERVICE_TOKEN_PATH;
});

describe('who may watch', () => {
	it('sends a browser with no session to sign in rather than to the stream', async () => {
		const event = request('/events', browser(), { routeId: '/events' });

		expect(await throughHook(event)).toMatchObject({
			redirectedTo: expect.stringContaining('/login')
		});
	});

	it('lets a viewer watch, because watching is reading', async () => {
		const event = request('/events', await signedIn('viewer'), { routeId: '/events' });

		expect(await throughHook(event)).toMatchObject({ allowed: expect.anything() });
	});
});

describe('the frames a browser receives', () => {
	it('opens by asking for a snapshot, before anything incremental', async () => {
		const watcher = await watch(await signedIn('operator'));

		const opening = await watcher.next();

		expect(opening.name).toBe('resync');
		expect(JSON.parse(opening.data)).toEqual({ kind: 'resync', reason: 'stream_opened' });
		await watcher.leave();
	});

	it('carries the daemon event id as the cursor and its identifiers unchanged', async () => {
		const watcher = await watchAttached(await signedIn('operator'));
		await watcher.next();

		stream.send(
			daemonEvent({
				event_id: 41,
				generation: 12,
				type: 'operation.changed',
				data: { operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c', state: 'RUNNING' }
			})
		);

		const frame = await watcher.next();
		expect(frame.id).toBe('41');
		expect(frame.name).toBe('operation.changed');
		expect(JSON.parse(frame.data)).toMatchObject({
			event_id: 41,
			generation: 12,
			data: { operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c' }
		});
		await watcher.leave();
	});

	it('puts no cursor on a notice, so a reconnection names a position that existed', async () => {
		const watcher = await watch(await signedIn('operator'));

		expect((await watcher.next()).id).toBeUndefined();
		await watcher.leave();
	});

	it('never lets an event type end the frame it is written into', async () => {
		// A daemon that has gone wrong must not be able to compose a second frame
		// inside the first.
		const watcher = await watchAttached(await signedIn('operator'));
		await watcher.next();

		stream.send(daemonEvent({ event_id: 2, type: 'ok\nid: 999\ndata: forged' }));

		const frame = await watcher.next();
		expect(frame.id).toBe('2');
		expect(JSON.parse(frame.data)).toMatchObject({ event_id: 2 });
		await watcher.leave();
	});
});

describe('a browser that comes back', () => {
	it('resumes after the cursor it last saw, without re-reading a snapshot', async () => {
		const cookies = await signedIn('operator');
		const first = await watchAttached(cookies);
		await first.next();
		stream.send(daemonEvent({ event_id: 1 }));
		stream.send(daemonEvent({ event_id: 2 }));
		await first.take(2);

		const resumed = await watch(cookies, '1');

		const frame = await resumed.next();
		expect(frame.name).not.toBe('resync');
		expect(frame.id).toBe('2');
		await first.leave();
		await resumed.leave();
	});

	it('is sent to a snapshot when its cursor is one this process cannot resume', async () => {
		const watcher = await watch(await signedIn('operator'), '4096');

		expect(JSON.parse((await watcher.next()).data)).toEqual({
			kind: 'resync',
			reason: 'cursor_too_old'
		});
		await watcher.leave();
	});

	it('treats a cursor it cannot read as no cursor at all', async () => {
		const watcher = await watch(await signedIn('operator'), 'not-a-position');

		expect(JSON.parse((await watcher.next()).data)).toEqual({
			kind: 'resync',
			reason: 'stream_opened'
		});
		await watcher.leave();
	});
});

describe('one reader, many browsers', () => {
	it('opens a single connection to the daemon however many are watching', async () => {
		const cookies = await signedIn('operator');
		const one = await watchAttached(cookies);
		await one.next();
		const two = await watch(cookies);

		stream.send(daemonEvent({ event_id: 3 }));

		expect(await two.take(2)).toMatchObject([{ name: 'resync' }, { id: '3' }]);
		expect(daemon.requests.filter((sent) => sent.path === '/v1/events')).toHaveLength(1);
		await one.leave();
		await two.leave();
	});

	it('keeps delivering to the browsers that stayed when one goes away', async () => {
		const cookies = await signedIn('operator');
		const staying = await watchAttached(cookies);
		const leaving = await watch(cookies);
		await Promise.all([staying.next(), leaving.next()]);
		await leaving.leave();

		stream.send(daemonEvent({ event_id: 5 }));

		expect((await staying.next()).id).toBe('5');
		await staying.leave();
	});
});

describe('restarting this service', () => {
	it('neither cancels nor duplicates the operation the daemon is running', async () => {
		// The daemon owns the operation. This service holding a database handle and
		// a reader is all that a restart drops, which is why it cannot reach into
		// what the machine is doing.
		const cookies = await signedIn('operator');
		await pressStop(cookies);
		const watcher = await watch(cookies);
		await watcher.next();
		const commandsBefore = daemon.requests.filter((sent) => sent.method === 'POST');

		await watcher.leave();
		closeWebRuntime();
		stream.end();
		const restarted = await watch(await signedIn('viewer'));
		await restarted.next();

		expect(commandsBefore).toHaveLength(1);
		expect(daemon.requests.filter((sent) => sent.method === 'POST')).toEqual(commandsBefore);
		await restarted.leave();
	});

	it('sends the browser that reconnects across it to a snapshot', async () => {
		// Whatever this process had retained went with it, so the cursor the
		// browser is holding cannot be shown to be continuous with what follows.
		const cookies = await signedIn('operator');
		const before = await watchAttached(cookies);
		await before.next();
		stream.send(daemonEvent({ event_id: 7 }));
		await before.next();

		await before.leave();
		closeWebRuntime();
		stream.end();
		const after = await watch(await signedIn('viewer'), '7');

		expect(JSON.parse((await after.next()).data)).toEqual({
			kind: 'resync',
			reason: 'cursor_too_old'
		});
		await after.leave();
	});
});

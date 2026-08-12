/**
 * One daemon reader, many browsers.
 *
 * What matters here is not that events arrive — it is what happens when they
 * cannot. A browser that stops reading must not slow anything down, a browser
 * that falls behind must be told to re-read rather than shown a gap, and a
 * reader that drops must not leave anyone believing they are still up to date.
 */

import { afterEach, describe, expect, it } from 'vitest';

import { EventBroadcast, type BrowserMessage, type UpstreamSource } from './broadcast';
import { DaemonError } from './errors';
import type { DaemonEvent, StreamMessage } from './events';

/** A daemon stream a spec can push into, with the connections it received. */
class FakeUpstream {
	/** The cursor each attachment asked to resume from, in order. */
	readonly connections: (number | undefined)[] = [];
	#queue: StreamMessage[] = [];
	#wake: (() => void) | undefined;

	readonly source: UpstreamSource = (where) => {
		this.connections.push(where.from);
		return this.#drain(where.signal);
	};

	push(...messages: StreamMessage[]): void {
		this.#queue.push(...messages);
		const wake = this.#wake;
		this.#wake = undefined;
		wake?.();
	}

	send(...events: DaemonEvent[]): void {
		this.push(...events.map((event) => ({ kind: 'event', event }) as const));
	}

	async *#drain(signal: AbortSignal): AsyncGenerator<StreamMessage, void, void> {
		while (!signal.aborted) {
			const next = this.#queue.shift();
			if (next !== undefined) {
				yield next;
				continue;
			}
			await new Promise<void>((resolve) => {
				this.#wake = resolve;
				signal.addEventListener('abort', () => resolve(), { once: true });
			});
		}
	}
}

/** A source that cannot be established at all. */
const refusing: UpstreamSource = () => {
	throw new DaemonError({ kind: 'unreachable', detail: 'no socket' });
};

function event(overrides: Partial<DaemonEvent> = {}): DaemonEvent {
	return {
		api_version: 'v1',
		event_id: 1,
		occurred_at: '2026-08-11T12:00:00.000Z',
		generation: 7,
		type: 'snapshot.changed',
		data: {},
		...overrides
	} as DaemonEvent;
}

interface Watcher {
	next: () => Promise<BrowserMessage>;
	take: (count: number) => Promise<BrowserMessage[]>;
	leave: () => Promise<void>;
}

/**
 * One browser.
 *
 * It leaves the way a real one does — by its request being aborted — rather
 * than by closing the generator, which would not run while it is waiting.
 */
function watch(hub: EventBroadcast, options: { from?: number } = {}): Watcher {
	const closed = new AbortController();
	const stream = hub.subscribe({ ...options, signal: closed.signal });
	return {
		next: async () => (await stream.next()).value as BrowserMessage,
		take: async (count) => {
			const seen: BrowserMessage[] = [];
			while (seen.length < count) {
				seen.push((await stream.next()).value as BrowserMessage);
			}
			return seen;
		},
		leave: async () => {
			closed.abort();
			let step = await stream.next();
			while (step.done !== true) {
				step = await stream.next();
			}
		}
	};
}

/** Let the hub's pump reach the point where it has asked for a connection. */
const settle = () => new Promise<void>((resolve) => setImmediate(resolve));

let hub: EventBroadcast;

function broadcasting(
	upstream: UpstreamSource,
	sizes: { backlogLimit?: number; replayCapacity?: number } = {}
) {
	hub = new EventBroadcast({
		socketPath: '/nowhere/s',
		serviceTokenPath: '/nowhere/token',
		source: upstream,
		...sizes
	});
	return hub;
}

afterEach(() => {
	hub?.close();
});

describe('fanning out', () => {
	it('gives the same event to every browser watching', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const one = watch(broadcast);
		const two = watch(broadcast);
		await Promise.all([one.next(), two.next()]);

		upstream.send(event({ event_id: 4 }));

		expect([await one.next(), await two.next()]).toEqual([
			{ kind: 'event', event: event({ event_id: 4 }) },
			{ kind: 'event', event: event({ event_id: 4 }) }
		]);
	});

	it('reads the daemon once, however many browsers are watching', async () => {
		// A second connection would buy nothing and would spend the daemon's
		// retention budget on a stream identical to the first.
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		await watch(broadcast).next();
		await watch(broadcast).next();
		await settle();

		expect(upstream.connections).toHaveLength(1);
	});

	it('holds no connection open when nobody is watching', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const only = watch(broadcast);
		await only.next();
		expect(broadcast.attached).toBe(true);

		await only.leave();

		expect(broadcast.attached).toBe(false);
	});

	it('resumes the daemon stream where it left off when someone watches again', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const first = watch(broadcast);
		await first.next();
		upstream.send(event({ event_id: 9 }));
		await first.next();
		await first.leave();

		await watch(broadcast).next();
		await settle();

		expect(upstream.connections).toEqual([undefined, 9]);
	});
});

describe('opening a stream', () => {
	it('sends a browser with no cursor to a snapshot before anything incremental', async () => {
		const broadcast = broadcasting(new FakeUpstream().source);

		expect(await watch(broadcast).next()).toEqual({ kind: 'resync', reason: 'stream_opened' });
	});

	it('replays what a resuming browser missed, without sending it to a snapshot', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const first = watch(broadcast);
		await first.next();
		upstream.send(event({ event_id: 1 }), event({ event_id: 2 }), event({ event_id: 3 }));
		await first.take(3);

		const resumed = watch(broadcast, { from: 1 });

		expect(await resumed.take(2)).toEqual([
			{ kind: 'event', event: event({ event_id: 2 }) },
			{ kind: 'event', event: event({ event_id: 3 }) }
		]);
	});

	it('sends a browser whose cursor is older than what is retained to a snapshot', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source, { replayCapacity: 2 });
		const first = watch(broadcast);
		await first.next();
		upstream.send(event({ event_id: 1 }), event({ event_id: 2 }), event({ event_id: 3 }));
		await first.take(3);

		expect(await watch(broadcast, { from: 1 }).next()).toEqual({
			kind: 'resync',
			reason: 'cursor_too_old'
		});
	});

	it('sends a browser whose cursor names a position this process never saw to a snapshot', async () => {
		// A cursor from before a restart of this service, for instance. It cannot
		// be proved continuous, so it is not treated as if it were.
		const broadcast = broadcasting(new FakeUpstream().source);

		expect(await watch(broadcast, { from: 4_096 }).next()).toEqual({
			kind: 'resync',
			reason: 'cursor_too_old'
		});
	});

	it('retains no more than its capacity, so a quiet browser cannot grow this process', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source, { replayCapacity: 3 });
		const first = watch(broadcast);
		await first.next();
		upstream.send(...[1, 2, 3, 4, 5].map((event_id) => event({ event_id })));
		await first.take(5);

		expect(await watch(broadcast, { from: 3 }).take(2)).toEqual([
			{ kind: 'event', event: event({ event_id: 4 }) },
			{ kind: 'event', event: event({ event_id: 5 }) }
		]);
	});
});

describe('a browser that falls behind', () => {
	it('is told to read a snapshot rather than handed a backlog it cannot use', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source, { backlogLimit: 2 });
		const slow = watch(broadcast);
		await slow.next();

		upstream.send(...[1, 2, 3, 4, 5].map((event_id) => event({ event_id })));
		await settle();

		expect(await slow.next()).toEqual({ kind: 'resync', reason: 'overflow' });
	});

	it('goes on receiving what happens after that, having been sent to a snapshot', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source, { backlogLimit: 3 });
		const slow = watch(broadcast);
		await slow.next();
		upstream.send(...[1, 2, 3, 4].map((event_id) => event({ event_id })));
		await settle();
		upstream.send(event({ event_id: 5 }));
		await settle();

		// The events after the notice still arrive; the snapshot it is about to
		// read carries the generation that lets it discard the ones it predates.
		expect(await slow.take(2)).toEqual([
			{ kind: 'resync', reason: 'overflow' },
			{ kind: 'event', event: event({ event_id: 5 }) }
		]);
	});

	it('never holds up the daemon reader or the browsers that are keeping up', async () => {
		// This is the criterion that a browser disconnect cannot block daemon event
		// production: the silent watcher below never reads a thing, and the one
		// beside it still sees every event in order.
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source, { backlogLimit: 4 });
		const silent = watch(broadcast);
		const attentive = watch(broadcast);
		await Promise.all([silent.next(), attentive.next()]);

		const seen: number[] = [];
		for (let event_id = 1; event_id <= 200; event_id += 1) {
			upstream.send(event({ event_id }));
			const message = await attentive.next();
			if (message.kind === 'event') {
				seen.push(message.event.event_id);
			}
		}

		expect(seen).toHaveLength(200);
		expect(broadcast.attached).toBe(true);
	});
});

describe('what the daemon says', () => {
	it('passes an event through with its identifiers untouched', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const only = watch(broadcast);
		await only.next();
		const original = event({
			event_id: 41,
			generation: 12,
			type: 'operation.changed',
			data: { operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c', state: 'RUNNING' }
		});

		upstream.send(original);

		expect(await only.next()).toEqual({ kind: 'event', event: original });
	});

	it('passes an event type this build does not know through unchanged', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const only = watch(broadcast);
		await only.next();

		upstream.send(event({ event_id: 2, type: 'something.added.in.a.later.daemon' }));

		expect(await only.next()).toMatchObject({
			kind: 'event',
			event: { type: 'something.added.in.a.later.daemon' }
		});
	});

	it('tells every browser to resynchronise when the daemon rejects the cursor', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const only = watch(broadcast);
		await only.next();

		upstream.push({ kind: 'resync', reason: 'cursor_too_old' });

		expect(await only.next()).toEqual({ kind: 'resync', reason: 'cursor_too_old' });
	});

	it('stops offering what it retained once continuity is broken', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const first = watch(broadcast);
		await first.next();
		upstream.send(event({ event_id: 1 }));
		await first.next();

		upstream.push({ kind: 'resync', reason: 'reconnected' });
		await first.next();

		expect(await watch(broadcast, { from: 1 }).next()).toEqual({
			kind: 'resync',
			reason: 'cursor_too_old'
		});
	});

	it('reports a reconnection as a resynchronisation, because a gap is possible', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const only = watch(broadcast);
		await only.next();

		upstream.push({ kind: 'resync', reason: 'reconnected' });

		expect(await only.next()).toEqual({ kind: 'resync', reason: 'reconnected' });
	});

	it('reports an unreachable daemon in this workspace words, not the daemon report', async () => {
		const upstream = new FakeUpstream();
		const broadcast = broadcasting(upstream.source);
		const only = watch(broadcast);
		await only.next();

		upstream.push({
			kind: 'disconnected',
			error: new DaemonError({ kind: 'unreachable', detail: '/run/cs71d/cs71d.sock' })
		});

		const message = await only.next();
		expect(message).toMatchObject({ kind: 'unavailable' });
		expect(JSON.stringify(message)).not.toContain('/run/cs71d');
	});

	it('says so and ends the stream when the reader cannot be established at all', async () => {
		// An unreadable service credential, typically. Ending is what makes the
		// browser reconnect, which is where the retry belongs.
		const broadcast = broadcasting(refusing);
		const only = watch(broadcast);

		expect(await only.take(2)).toEqual([
			{ kind: 'resync', reason: 'stream_opened' },
			{ kind: 'unavailable', message: expect.stringContaining('not answering') }
		]);
	});
});

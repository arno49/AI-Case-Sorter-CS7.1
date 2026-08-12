/**
 * Staying attached to the daemon's stream, against a daemon really streaming.
 *
 * The properties under test are the ones a browser depends on and cannot check
 * for itself: that the daemon's own identifiers arrive unrenumbered, that a gap
 * is announced rather than papered over, and that a reconnection re-attaches a
 * reader without ever resending anything the machine might act on.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { DaemonError } from './errors';
import {
	MAXIMUM_EVENT_BYTES,
	readDaemonEvents,
	subscribeToDaemonEvents,
	type DaemonEvent,
	type StreamMessage
} from './events';
import {
	SERVICE_TOKEN,
	daemonEvent,
	eventStream,
	replying,
	startFakeDaemon,
	type FakeDaemon,
	type FakeEventStream
} from './harness';

let daemon: FakeDaemon;
let stream: FakeEventStream;

function reader(lastEventId?: number): AsyncGenerator<DaemonEvent, void, void> {
	return readDaemonEvents({
		socketPath: daemon.socketPath,
		serviceToken: SERVICE_TOKEN,
		lastEventId
	});
}

function subscription(from?: number): AsyncGenerator<StreamMessage, void, void> {
	return subscribeToDaemonEvents({
		socketPath: daemon.socketPath,
		serviceToken: SERVICE_TOKEN,
		from,
		backoffMs: [0],
		wait: async () => {}
	});
}

/** Collect until `count` messages have arrived, then let the generator finish. */
async function collect(
	messages: AsyncGenerator<StreamMessage, void, void>,
	count: number
): Promise<StreamMessage[]> {
	const collected: StreamMessage[] = [];
	for await (const message of messages) {
		collected.push(message);
		if (collected.length >= count) {
			break;
		}
	}
	return collected;
}

beforeEach(async () => {
	daemon = await startFakeDaemon();
	stream = eventStream();
	daemon.answerWith(stream.handler);
});

afterEach(async () => {
	stream.end();
	await daemon.close();
});

describe('one connection to the stream', () => {
	it('delivers the daemon identifiers unrenumbered', async () => {
		const attached = stream.nextConnection();
		const events = reader();
		const first = events.next();
		await attached;

		stream.send(
			daemonEvent({
				event_id: 41,
				generation: 9,
				type: 'operation.progress',
				operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c'
			})
		);

		// A bridge that invented its own sequence would make the two ends
		// impossible to line up when something went wrong.
		expect((await first).value).toMatchObject({
			event_id: 41,
			generation: 9,
			operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c'
		});
		// Not awaited: a reader still waiting on a silent socket would only
		// finish once the daemon spoke, and the fixture is torn down next.
		void events.return();
	});

	it('presents the service credential and asks for an event stream', async () => {
		const attached = stream.nextConnection();
		const events = reader();
		void events.next();
		await attached;

		expect(daemon.lastRequest().headers.authorization).toBe(`Bearer ${SERVICE_TOKEN}`);
		expect(daemon.lastRequest().headers.accept).toBe('text/event-stream');
		expect(daemon.lastRequest().path).toBe('/v1/events');
		// Not awaited: a reader still waiting on a silent socket would only
		// finish once the daemon spoke, and the fixture is torn down next.
		void events.return();
	});

	it('asks to continue after the cursor it was given', async () => {
		const attached = stream.nextConnection();
		const events = reader(17);
		void events.next();
		await attached;

		expect(stream.cursors).toEqual(['17']);
		// Not awaited: a reader still waiting on a silent socket would only
		// finish once the daemon spoke, and the fixture is torn down next.
		void events.return();
	});

	it('skips a frame it cannot read rather than dropping the stream', async () => {
		const attached = stream.nextConnection();
		const events = reader();
		const first = events.next();
		await attached;

		stream.raw('data: not json\n\n');
		stream.raw(':a comment, which carries no data\n\n');
		stream.send(daemonEvent({ event_id: 2 }));

		expect((await first).value).toMatchObject({ event_id: 2 });
		// Not awaited: a reader still waiting on a silent socket would only
		// finish once the daemon spoke, and the fixture is torn down next.
		void events.return();
	});

	it('passes through an event type this build does not know', async () => {
		// The contract says a v1 consumer ignores unknown names; ignoring is the
		// consumer's decision, not something to lose in transit.
		const attached = stream.nextConnection();
		const events = reader();
		const first = events.next();
		await attached;

		stream.send(daemonEvent({ type: 'something.new' }));

		expect((await first).value).toMatchObject({ type: 'something.new' });
		// Not awaited: a reader still waiting on a silent socket would only
		// finish once the daemon spoke, and the fixture is torn down next.
		void events.return();
	});

	it('refuses a stream the daemon would not open', async () => {
		daemon.answerWith(replying(401, { api_version: 'v1', code: 'UNAUTHENTICATED' }));

		await expect(reader().next()).rejects.toBeInstanceOf(DaemonError);
	});

	it('gives up on an event larger than it will hold', async () => {
		const attached = stream.nextConnection();
		const events = reader();
		const first = events.next();
		await attached;

		stream.raw(`data: ${'x'.repeat(MAXIMUM_EVENT_BYTES + 1)}`);

		await expect(first).rejects.toBeInstanceOf(DaemonError);
	});
});

describe('staying attached', () => {
	it('reports a cursor the daemon will not honour, so the browser re-reads a snapshot', async () => {
		const attached = stream.nextConnection();
		const messages = subscription(3);
		const collected = collect(messages, 1);
		await attached;

		stream.send(daemonEvent({ event_id: 0, type: 'snapshot.required' }));

		expect(await collected).toEqual([{ kind: 'resync', reason: 'cursor_too_old' }]);
	});

	it('stops presenting that cursor once the daemon has rejected it', async () => {
		const attached = stream.nextConnection();
		const messages = subscription(3);
		// Three: the refusal, the gap announced by reconnecting, and an event on
		// the second connection, which is what proves the reconnection happened.
		const collected = collect(messages, 3);
		await attached;

		const reconnected = stream.nextConnection();
		stream.send(daemonEvent({ event_id: 0, type: 'snapshot.required' }));
		stream.end();
		await reconnected;
		stream.send(daemonEvent({ event_id: 9 }));
		await collected;

		// The first connection asked to continue after 3; the next must not, since
		// asking again with a cursor the daemon has rejected would loop.
		expect(stream.cursors).toEqual(['3', undefined]);
	});

	it('reconnects when the daemon ends the stream, and says the gap is there', async () => {
		const attached = stream.nextConnection();
		const messages = subscription();
		const collected = collect(messages, 2);
		await attached;

		stream.send(daemonEvent({ event_id: 4 }));
		stream.end();

		const [first, second] = await collected;
		expect(first).toMatchObject({ kind: 'event', event: { event_id: 4 } });
		// Even an honoured cursor does not prove nothing was missed, and a browser
		// that redraws from a snapshot after a gap is correct where one that keeps
		// applying increments is guessing.
		expect(second).toEqual({ kind: 'resync', reason: 'reconnected' });
	});

	it('resumes from the last event it actually delivered', async () => {
		const attached = stream.nextConnection();
		const messages = subscription();
		const collected = collect(messages, 3);
		await attached;

		const reconnected = stream.nextConnection();
		stream.send(daemonEvent({ event_id: 4 }));
		stream.end();
		await reconnected;
		stream.send(daemonEvent({ event_id: 5 }));
		await collected;

		expect(stream.cursors).toEqual([undefined, '4']);
	});

	it('reports a daemon that is not there, and keeps trying', async () => {
		await daemon.close();

		expect((await collect(subscription(), 1))[0]).toMatchObject({ kind: 'disconnected' });
	});

	it('stops when it is told to, without another connection', async () => {
		const controller = new AbortController();
		const attached = stream.nextConnection();
		const messages = subscribeToDaemonEvents({
			socketPath: daemon.socketPath,
			serviceToken: SERVICE_TOKEN,
			signal: controller.signal,
			backoffMs: [0],
			wait: async () => {}
		});
		const first = messages.next();
		await attached;
		stream.send(daemonEvent({ event_id: 1 }));
		await first;

		controller.abort();
		const connections = stream.cursors.length;

		expect(await messages.next()).toEqual({ done: true, value: undefined });
		expect(stream.cursors.length).toBe(connections);
	});
});

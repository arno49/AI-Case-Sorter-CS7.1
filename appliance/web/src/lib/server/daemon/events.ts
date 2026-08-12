/**
 * Reading the daemon's event stream, and staying attached to it.
 *
 * This is the only place that reconnects to `cs71d` on its own. It is safe to
 * do here and nowhere else, because reading is the only thing that can be
 * retried without asking whether the machine already did it: a command that
 * timed out may have moved the machine, and no code in this workspace resends
 * one.
 *
 * The daemon's cursor is what makes resumption honest. Each event carries a
 * monotonic `event_id`; a reconnection asks to continue after the last one this
 * process actually delivered, and the daemon answers either with what came
 * after it or with `snapshot.required` when that cursor is too old to honour.
 * A consumer that sees `snapshot.required` must read a snapshot before showing
 * anything incremental again — the alternative is a screen assembled from a gap.
 *
 * Nothing here interprets an event. The `event_id`, `operation_id` and
 * `generation` a browser is shown are the daemon's own, unrenumbered, because a
 * bridge that invents its own sequence would make the two ends impossible to
 * line up when something goes wrong.
 */

import { request as httpRequest, type IncomingMessage } from 'node:http';

import type { components } from '$lib/api/cs71d-v1';
import { DaemonError } from './errors';

export type DaemonEvent = components['schemas']['DaemonEvent'];

/** The daemon's own name for "your cursor is too old; re-read the snapshot". */
export const RESYNC_EVENT = 'snapshot.required';

/**
 * A single event may not exceed this. The stream is unbounded by nature, so
 * the bound has to be per frame, and a daemon that sends more than this has
 * gone wrong in a way that must not become this process running out of memory.
 */
export const MAXIMUM_EVENT_BYTES = 64 * 1024;

/** How long a stream may say nothing at all before it is treated as dead. */
export const IDLE_TIMEOUT_MS = 60_000;

export interface EventStreamOptions {
	readonly socketPath: string;
	readonly serviceToken: string;
	/** Continue after this daemon cursor; omitted asks for the live position. */
	readonly lastEventId?: number;
	readonly signal?: AbortSignal;
	readonly idleTimeoutMs?: number;
}

/**
 * One connection to the daemon's stream.
 *
 * Yields events in the order the daemon sent them, ends when the daemon ends
 * the response, and throws a `DaemonError` when the connection fails or the
 * daemon refuses.
 */
export async function* readDaemonEvents(
	options: EventStreamOptions
): AsyncGenerator<DaemonEvent, void, void> {
	const response = await connect(options);
	try {
		for await (const frame of frames(response, options.signal)) {
			const event = parseEvent(frame);
			if (event !== undefined) {
				yield event;
			}
		}
	} finally {
		response.destroy();
	}
}

/** What a bridge sees: events, and the two things that are not events. */
export type StreamMessage =
	| { readonly kind: 'event'; readonly event: DaemonEvent }
	/** Read a snapshot before presenting anything incremental again. */
	| { readonly kind: 'resync'; readonly reason: 'cursor_too_old' | 'reconnected' }
	/** The stream dropped; a reconnection is being attempted. */
	| { readonly kind: 'disconnected'; readonly error: DaemonError };

export interface SubscriptionOptions extends Omit<EventStreamOptions, 'lastEventId'> {
	readonly from?: number;
	/** Waits between reconnection attempts, in order; the last one repeats. */
	readonly backoffMs?: readonly number[];
	/** Sleep, injected so a spec does not have to wait out a backoff. */
	readonly wait?: (ms: number) => Promise<void>;
}

const DEFAULT_BACKOFF_MS: readonly number[] = [250, 500, 1_000, 2_000, 5_000];

/**
 * Stay attached to the daemon's stream across restarts of the daemon.
 *
 * A reconnection is not a resumption of anything the machine was doing: the
 * daemon owns the operation and keeps running it whether or not this service is
 * listening. This only re-attaches a reader, which is why it may retry at all.
 *
 * Every reconnection emits `resync` before any further event. Even when the
 * cursor is honoured, this process cannot promise it saw everything in between,
 * and a browser that redraws from a snapshot after a gap is correct where one
 * that keeps applying increments is guessing.
 */
export async function* subscribeToDaemonEvents(
	options: SubscriptionOptions
): AsyncGenerator<StreamMessage, void, void> {
	const backoff = options.backoffMs ?? DEFAULT_BACKOFF_MS;
	const wait = options.wait ?? sleep;
	let cursor = options.from;
	let attempt = 0;
	let firstConnection = true;

	const aborted = () => options.signal?.aborted === true;

	while (!aborted()) {
		try {
			if (!firstConnection) {
				yield { kind: 'resync', reason: 'reconnected' };
			}
			for await (const event of readDaemonEvents({ ...options, lastEventId: cursor })) {
				attempt = 0;
				firstConnection = false;
				if (event.type === RESYNC_EVENT) {
					// The daemon refused the cursor. Forget it: asking again with a
					// cursor it has already rejected would loop.
					cursor = undefined;
					yield { kind: 'resync', reason: 'cursor_too_old' };
					continue;
				}
				cursor = event.event_id;
				yield { kind: 'event', event };
			}
			// The daemon ended the response. Reconnect on the same terms.
			firstConnection = false;
		} catch (error) {
			if (aborted()) {
				return;
			}
			firstConnection = false;
			yield {
				kind: 'disconnected',
				error: error instanceof DaemonError ? error : new DaemonError({ kind: 'unreachable' })
			};
		}

		if (aborted()) {
			return;
		}
		await wait(backoff[Math.min(attempt, backoff.length - 1)]);
		attempt += 1;
	}
}

function connect(options: EventStreamOptions): Promise<IncomingMessage> {
	return new Promise<IncomingMessage>((resolve, reject) => {
		const outgoing = httpRequest(
			{
				socketPath: options.socketPath,
				path: '/v1/events',
				method: 'GET',
				headers: {
					authorization: `Bearer ${options.serviceToken}`,
					accept: 'text/event-stream',
					...(options.lastEventId === undefined
						? {}
						: { 'last-event-id': String(options.lastEventId) })
				}
			},
			(incoming) => {
				if (incoming.statusCode !== 200) {
					incoming.resume();
					reject(
						new DaemonError({
							kind: 'rejected',
							status: incoming.statusCode,
							detail: 'the daemon refused the event stream'
						})
					);
					return;
				}
				resolve(incoming);
			}
		);

		// A stream that says nothing at all is indistinguishable from a dead
		// socket, and the daemon sends heartbeats precisely so it can be told
		// apart. Silence past the idle window is treated as a disconnection.
		outgoing.setTimeout(options.idleTimeoutMs ?? IDLE_TIMEOUT_MS, () => {
			outgoing.destroy(new DaemonError({ kind: 'timeout', detail: 'the event stream went quiet' }));
		});

		outgoing.on('error', (cause) => {
			reject(
				cause instanceof DaemonError
					? cause
					: new DaemonError({
							kind: 'unreachable',
							detail: `cannot reach the daemon at ${options.socketPath}`,
							cause
						})
			);
		});

		options.signal?.addEventListener('abort', () => outgoing.destroy(), { once: true });
		outgoing.end();
	});
}

/** Split the response into SSE frames, refusing one that grows past the bound. */
async function* frames(
	response: IncomingMessage,
	signal?: AbortSignal
): AsyncGenerator<string, void, void> {
	let buffer = '';
	for await (const chunk of response) {
		if (signal?.aborted === true) {
			return;
		}
		buffer += (chunk as Buffer).toString('utf8');
		let boundary = buffer.indexOf('\n\n');
		while (boundary !== -1) {
			yield buffer.slice(0, boundary);
			buffer = buffer.slice(boundary + 2);
			boundary = buffer.indexOf('\n\n');
		}
		if (buffer.length > MAXIMUM_EVENT_BYTES) {
			throw new DaemonError({
				kind: 'malformed',
				detail: `an event exceeded ${MAXIMUM_EVENT_BYTES} bytes`
			});
		}
	}
}

/**
 * Turn one frame into an event.
 *
 * The payload is the authority on `event_id`, not the SSE `id:` line: the
 * contract defines the cursor as a field of the event, and a frame whose
 * envelope disagrees with its body is not something to reconcile by guessing.
 * A frame this build does not understand is skipped rather than fatal — the
 * contract says v1 consumers ignore unknown event types.
 */
function parseEvent(frame: string): DaemonEvent | undefined {
	const data = frame
		.split('\n')
		.filter((line) => line.startsWith('data:'))
		.map((line) => line.slice('data:'.length).trimStart())
		.join('\n');
	if (data === '') {
		return undefined;
	}

	let payload: unknown;
	try {
		payload = JSON.parse(data);
	} catch {
		return undefined;
	}

	if (!isDaemonEvent(payload)) {
		return undefined;
	}
	return payload;
}

function isDaemonEvent(payload: unknown): payload is DaemonEvent {
	if (typeof payload !== 'object' || payload === null) {
		return false;
	}
	const candidate = payload as Record<string, unknown>;
	return (
		candidate.api_version === 'v1' &&
		typeof candidate.event_id === 'number' &&
		Number.isInteger(candidate.event_id) &&
		typeof candidate.type === 'string' &&
		typeof candidate.generation === 'number'
	);
}

function sleep(ms: number): Promise<void> {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

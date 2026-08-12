/**
 * One reader of the daemon's stream, however many browsers are watching.
 *
 * The daemon is the machine's only owner, and a second, third and fourth
 * connection to its event stream would buy nothing: the events are identical for
 * every viewer. So this process attaches once and hands the same events to every
 * open browser. That also puts the cost of a slow browser here, where dropping
 * one is cheap, rather than upstream, where it would make the daemon spend its
 * retention budget on a screen nobody is looking at.
 *
 * A browser is never allowed to hold anything up. If it falls far enough behind
 * that its backlog is worthless, the backlog is discarded and it is told to read
 * a snapshot — more events cannot catch up a viewer that is that far out of
 * date, only a fresh snapshot can. Nothing here ever waits for a browser, so a
 * browser that stops reading, or vanishes without closing, cannot slow the
 * daemon's event production or the serial worker behind it.
 *
 * Identifiers stay the daemon's. This retains recent events so a browser whose
 * connection blinked can resume exactly where it was, but it never renumbers
 * one: `event_id`, `operation_id` and `generation` are the daemon's own, so the
 * two ends can still be lined up when something goes wrong.
 */

import { readServiceToken } from './credentials';
import { safeResponseFor } from './errors';
import { subscribeToDaemonEvents, type DaemonEvent, type StreamMessage } from './events';

/**
 * How many recent events this process keeps.
 *
 * This exists so a browser whose connection dropped for a moment can resume
 * without re-reading a snapshot. It is not durable history and not a substitute
 * for the daemon's own retention; a cursor older than this is answered with a
 * resynchronisation rather than a guess.
 */
export const REPLAY_CAPACITY = 256;

/** How far one browser may fall behind before it is resynchronised instead. */
export const SUBSCRIBER_BACKLOG_LIMIT = 64;

export type ResyncReason =
	/** A new stream with no cursor to resume from. */
	| 'stream_opened'
	/** The cursor is outside what this process, or the daemon, still has. */
	| 'cursor_too_old'
	/** The link to the daemon dropped and came back; a gap is possible. */
	| 'reconnected'
	/** This browser fell behind and its backlog was discarded. */
	| 'overflow';

/** What one browser's stream carries. */
export type BrowserMessage =
	| { readonly kind: 'event'; readonly event: DaemonEvent }
	/** Read a snapshot before presenting anything incremental again. */
	| { readonly kind: 'resync'; readonly reason: ResyncReason }
	/** The daemon is not answering. Wording this workspace owns, never its own. */
	| { readonly kind: 'unavailable'; readonly message: string };

export interface SubscribeOptions {
	/** Resume after this daemon `event_id`. */
	readonly from?: number;
	readonly signal?: AbortSignal;
}

/** Where the events come from, injected so a spec need not own a socket. */
export type UpstreamSource = (options: {
	readonly from?: number;
	readonly signal: AbortSignal;
}) => AsyncGenerator<StreamMessage, void, void>;

export interface EventBroadcastOptions {
	readonly socketPath: string;
	readonly serviceTokenPath: string;
	readonly replayCapacity?: number;
	readonly backlogLimit?: number;
	readonly source?: UpstreamSource;
}

export class EventBroadcast {
	readonly #subscribers = new Set<Subscriber>();
	readonly #replayCapacity: number;
	readonly #backlogLimit: number;
	readonly #source: UpstreamSource;
	#replay: DaemonEvent[] = [];
	#upstream: AbortController | undefined;
	#token: string | undefined;

	constructor(private readonly options: EventBroadcastOptions) {
		this.#replayCapacity = options.replayCapacity ?? REPLAY_CAPACITY;
		this.#backlogLimit = options.backlogLimit ?? SUBSCRIBER_BACKLOG_LIMIT;
		this.#source = options.source ?? ((where) => this.#readDaemon(where));
	}

	/** How many browsers are attached. Evidence, and a stop condition. */
	get subscriberCount(): number {
		return this.#subscribers.size;
	}

	/** Whether this process is currently reading the daemon's stream. */
	get attached(): boolean {
		return this.#upstream !== undefined;
	}

	/**
	 * One browser's view of the stream.
	 *
	 * Ends when the browser goes away, or when the reader behind it stops; a
	 * browser whose stream ends reconnects, which is how a failed attachment gets
	 * retried without this class owning a retry policy of its own.
	 */
	async *subscribe(options: SubscribeOptions = {}): AsyncGenerator<BrowserMessage, void, void> {
		const subscriber = new Subscriber(this.#backlogLimit);
		for (const message of this.#opening(options.from)) {
			subscriber.offer(message);
		}
		this.#subscribers.add(subscriber);

		const detach = () => subscriber.close();
		options.signal?.addEventListener('abort', detach, { once: true });

		this.#attach();
		try {
			yield* subscriber.drain();
		} finally {
			options.signal?.removeEventListener('abort', detach);
			subscriber.close();
			this.#subscribers.delete(subscriber);
			this.#detachIfIdle();
		}
	}

	/** Drop everything: the process is shutting down, or a spec is ending. */
	close(): void {
		this.#upstream?.abort();
		this.#upstream = undefined;
		for (const subscriber of this.#subscribers) {
			subscriber.close();
		}
		this.#subscribers.clear();
		this.#replay = [];
	}

	/**
	 * What a newly attached browser is owed before anything live.
	 *
	 * A cursor is honoured only when this process can show the whole run of
	 * events since it. Anything else — no cursor, a cursor older than what is
	 * retained, a cursor from a position this process never saw — is a
	 * resynchronisation, because the alternative is a screen assembled from a gap.
	 */
	#opening(from: number | undefined): BrowserMessage[] {
		if (from === undefined) {
			return [{ kind: 'resync', reason: 'stream_opened' }];
		}
		const position = this.#replay.findIndex((event) => event.event_id === from);
		if (position === -1) {
			return [{ kind: 'resync', reason: 'cursor_too_old' }];
		}
		const missed = this.#replay.slice(position + 1);
		if (missed.length > this.#backlogLimit) {
			// More than one browser's backlog is owed. Handing it over would only
			// overflow the queue a moment later, so say what is true now: this
			// cursor cannot be resumed, read a snapshot.
			return [{ kind: 'resync', reason: 'cursor_too_old' }];
		}
		return missed.map((event) => ({ kind: 'event', event }) as const);
	}

	#attach(): void {
		if (this.#upstream !== undefined) {
			return;
		}
		const controller = new AbortController();
		this.#upstream = controller;
		void this.#pump(controller);
	}

	#detachIfIdle(): void {
		if (this.#subscribers.size === 0) {
			this.#upstream?.abort();
			this.#upstream = undefined;
		}
	}

	async #pump(controller: AbortController): Promise<void> {
		// Resume the upstream where this process left off, so a reader that was
		// stopped because nobody was watching does not silently skip what happened
		// while the appliance was idle. The daemon decides whether that cursor is
		// still honourable; when it is not, it says so and everyone resynchronises.
		const from = this.#replay.at(-1)?.event_id;
		try {
			for await (const message of this.#source({ from, signal: controller.signal })) {
				// A reader that has been replaced must not go on writing into the
				// replay behind the one that replaced it.
				if (controller.signal.aborted) {
					break;
				}
				this.#accept(message);
			}
		} catch (error) {
			// The reader could not be established at all — an unreadable service
			// credential is the usual cause. Say so once and let the browsers end,
			// rather than retrying a misconfiguration in a tight loop.
			if (!controller.signal.aborted) {
				this.#fanOut({ kind: 'unavailable', message: safeResponseFor(error).message });
			}
		} finally {
			if (this.#upstream === controller) {
				this.#upstream = undefined;
			}
			if (!controller.signal.aborted) {
				for (const subscriber of this.#subscribers) {
					subscriber.close();
				}
			}
		}
	}

	#accept(message: StreamMessage): void {
		if (message.kind === 'event') {
			this.#retain(message.event);
			this.#fanOut({ kind: 'event', event: message.event });
			return;
		}
		if (message.kind === 'resync') {
			// Continuity is broken, so what is retained can no longer be offered to
			// a late joiner as a complete run.
			this.#replay = [];
			this.#fanOut({
				kind: 'resync',
				reason: message.reason === 'cursor_too_old' ? 'cursor_too_old' : 'reconnected'
			});
			return;
		}
		this.#fanOut({ kind: 'unavailable', message: safeResponseFor(message.error).message });
	}

	#retain(event: DaemonEvent): void {
		this.#replay.push(event);
		if (this.#replay.length > this.#replayCapacity) {
			this.#replay.splice(0, this.#replay.length - this.#replayCapacity);
		}
	}

	#fanOut(message: BrowserMessage): void {
		for (const subscriber of this.#subscribers) {
			subscriber.offer(message);
		}
	}

	#readDaemon(where: {
		readonly from?: number;
		readonly signal: AbortSignal;
	}): AsyncGenerator<StreamMessage, void, void> {
		// Read once, as the command client does: the file is owner-only, and a
		// rotation is a service restart.
		this.#token ??= readServiceToken(this.options.serviceTokenPath);
		return subscribeToDaemonEvents({
			socketPath: this.options.socketPath,
			serviceToken: this.#token,
			from: where.from,
			signal: where.signal
		});
	}
}

/**
 * One browser's backlog.
 *
 * Offering never blocks and never rejects: the hub calls it while iterating the
 * daemon's stream, and a queue that pushed back there would put a browser's
 * reading speed in front of the machine's event production.
 */
class Subscriber {
	#queue: BrowserMessage[] = [];
	#wake: (() => void) | undefined;
	#closed = false;

	constructor(private readonly limit: number) {}

	offer(message: BrowserMessage): void {
		if (this.#closed) {
			return;
		}
		if (this.#queue.length >= this.limit) {
			// Too far behind to be caught up by more events. Discard the backlog and
			// send it to a snapshot; the events after this one are still delivered,
			// and the snapshot's generation is what lets it discard the stale ones.
			this.#queue = [{ kind: 'resync', reason: 'overflow' }];
		} else {
			this.#queue.push(message);
		}
		this.#signal();
	}

	close(): void {
		this.#closed = true;
		this.#signal();
	}

	async *drain(): AsyncGenerator<BrowserMessage, void, void> {
		while (!this.#closed || this.#queue.length > 0) {
			const next = this.#queue.shift();
			if (next !== undefined) {
				yield next;
				continue;
			}
			await new Promise<void>((resolve) => {
				this.#wake = resolve;
			});
		}
	}

	#signal(): void {
		const wake = this.#wake;
		this.#wake = undefined;
		wake?.();
	}
}

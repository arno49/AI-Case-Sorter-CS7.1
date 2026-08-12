/**
 * What a stranger on the network is allowed to spend.
 *
 * This is an appliance on a private LAN, not an Internet service, so the
 * budgets are small and the enforcement is in-process: there is exactly one
 * Node process, so an in-memory counter is the whole truth rather than a cache
 * of somebody else's.
 *
 * The cost being rationed is real. A login attempt costs an Argon2id hash at
 * 64 MiB of working memory, so a handful of concurrent attempts is enough to
 * matter on a Raspberry Pi. Both the rate and the concurrency of that work are
 * bounded, and the concurrency limit refuses rather than queues: a queue would
 * convert a burst into memory pressure plus a delay, and the person waiting
 * cannot tell those apart from a machine that has stopped answering.
 */

export interface RateLimitDecision {
	readonly allowed: boolean;
	/** How long until this key would be allowed again. */
	readonly retryAfterMs: number;
}

interface Window {
	count: number;
	resetAt: number;
}

/**
 * A fixed-window counter per key.
 *
 * Keys come from the network -- an address, a submitted username -- so the map
 * is bounded and evicts the windows closest to expiry when it is full. An
 * attacker who can invent keys can therefore cost memory only up to that bound.
 */
export class RateLimiter {
	readonly #windows = new Map<string, Window>();

	constructor(
		readonly limit: number,
		readonly windowMs: number,
		readonly maximumKeys = 4096
	) {}

	/** Record an attempt against `key` and say whether it is within budget. */
	check(key: string, now: Date): RateLimitDecision {
		const at = now.getTime();
		this.#evict(at);

		const window = this.#windows.get(key);
		if (window === undefined || window.resetAt <= at) {
			this.#windows.set(key, { count: 1, resetAt: at + this.windowMs });
			return { allowed: true, retryAfterMs: 0 };
		}

		window.count += 1;
		if (window.count > this.limit) {
			return { allowed: false, retryAfterMs: window.resetAt - at };
		}
		return { allowed: true, retryAfterMs: 0 };
	}

	/** Forget a key, so that succeeding costs nothing later. */
	forget(key: string): void {
		this.#windows.delete(key);
	}

	get size(): number {
		return this.#windows.size;
	}

	#evict(at: number): void {
		for (const [key, window] of this.#windows) {
			if (window.resetAt <= at) {
				this.#windows.delete(key);
			}
		}
		while (this.#windows.size >= this.maximumKeys) {
			const oldest = [...this.#windows].reduce((a, b) => (a[1].resetAt <= b[1].resetAt ? a : b));
			this.#windows.delete(oldest[0]);
		}
	}
}

export class TooBusyError extends Error {}

/** A cap on how much expensive work may be in flight at once. */
export class ConcurrencyLimit {
	#inFlight = 0;

	constructor(readonly maximum: number) {}

	get inFlight(): number {
		return this.#inFlight;
	}

	async run<T>(work: () => Promise<T>): Promise<T> {
		if (this.#inFlight >= this.maximum) {
			throw new TooBusyError('too much of this work is already in flight');
		}
		this.#inFlight += 1;
		try {
			return await work();
		} finally {
			this.#inFlight -= 1;
		}
	}
}

/** Documented budgets. Changing one changes what the README promises. */
export const LOGIN_ATTEMPT_LIMIT = 5;
export const LOGIN_WINDOW_MS = 60 * 1000;
export const CONCURRENT_LOGIN_LIMIT = 2;
export const STATE_CHANGE_LIMIT = 30;
export const STATE_CHANGE_WINDOW_MS = 60 * 1000;

/**
 * The largest body a form may submit.
 *
 * This is an early, cheap refusal on the declared length. The enforcement that
 * cannot be lied to is the adapter's own `BODY_SIZE_LIMIT`, which counts bytes
 * as they arrive.
 */
export const MAXIMUM_FORM_BYTES = 16 * 1024;

export interface WebLimits {
	/** Keyed by client address; covers every state-changing route. */
	readonly stateChanges: RateLimiter;
	/** Keyed by submitted username and by client address. */
	readonly logins: RateLimiter;
	readonly loginWork: ConcurrencyLimit;
}

export function createWebLimits(): WebLimits {
	return {
		stateChanges: new RateLimiter(STATE_CHANGE_LIMIT, STATE_CHANGE_WINDOW_MS),
		logins: new RateLimiter(LOGIN_ATTEMPT_LIMIT, LOGIN_WINDOW_MS),
		loginWork: new ConcurrencyLimit(CONCURRENT_LOGIN_LIMIT)
	};
}

/** Whether a request declares more content than it is allowed to send. */
export function declaresOversizedBody(request: Request, maximum = MAXIMUM_FORM_BYTES): boolean {
	const declared = Number(request.headers.get('content-length') ?? '0');
	return Number.isFinite(declared) && declared > maximum;
}

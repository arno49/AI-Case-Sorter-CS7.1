/**
 * The budgets, against an explicit clock.
 *
 * Nothing here sleeps: the limiter takes the instant as an argument precisely
 * so that a test of a one-minute window does not take a minute.
 */

import { describe, expect, it } from 'vitest';

import {
	ConcurrencyLimit,
	MAXIMUM_FORM_BYTES,
	RateLimiter,
	TooBusyError,
	createWebLimits,
	declaresOversizedBody
} from './limits';

const START = new Date('2026-08-11T12:00:00.000Z');

function at(offsetMs: number): Date {
	return new Date(START.getTime() + offsetMs);
}

describe('a rate limiter', () => {
	it('allows exactly its budget inside one window', () => {
		const limiter = new RateLimiter(3, 60_000);

		const decisions = [0, 1, 2, 3].map((attempt) => limiter.check('key', at(attempt)).allowed);

		expect(decisions).toEqual([true, true, true, false]);
	});

	it('says how long the caller has to wait', () => {
		const limiter = new RateLimiter(1, 60_000);
		limiter.check('key', START);

		expect(limiter.check('key', at(15_000)).retryAfterMs).toBe(45_000);
	});

	it('starts again once the window has passed', () => {
		const limiter = new RateLimiter(1, 60_000);
		limiter.check('key', START);
		expect(limiter.check('key', at(1)).allowed).toBe(false);

		expect(limiter.check('key', at(60_000)).allowed).toBe(true);
	});

	it('budgets each key separately', () => {
		const limiter = new RateLimiter(1, 60_000);
		limiter.check('one', START);

		expect(limiter.check('two', START).allowed).toBe(true);
	});

	it('forgets a key on request, so succeeding clears the cost of trying', () => {
		const limiter = new RateLimiter(1, 60_000);
		limiter.check('key', START);

		limiter.forget('key');

		expect(limiter.check('key', at(1)).allowed).toBe(true);
	});

	it('does not grow without bound when keys come from the network', () => {
		const limiter = new RateLimiter(1, 60_000, 8);

		for (let attempt = 0; attempt < 500; attempt += 1) {
			limiter.check(`address-${attempt}`, START);
		}

		expect(limiter.size).toBeLessThanOrEqual(8);
	});

	it('drops windows that have expired rather than keeping them forever', () => {
		const limiter = new RateLimiter(1, 60_000);
		limiter.check('key', START);

		limiter.check('other', at(60_001));

		expect(limiter.size).toBe(1);
	});
});

describe('a concurrency limit', () => {
	function pending(): { promise: Promise<void>; finish: () => void } {
		let finish = () => {};
		const promise = new Promise<void>((resolve) => {
			finish = resolve;
		});
		return { promise, finish };
	}

	it('runs work up to its maximum', async () => {
		const limit = new ConcurrencyLimit(2);
		const first = pending();
		const second = pending();

		const running = [limit.run(() => first.promise), limit.run(() => second.promise)];
		expect(limit.inFlight).toBe(2);

		first.finish();
		second.finish();
		await Promise.all(running);
		expect(limit.inFlight).toBe(0);
	});

	it('refuses rather than queueing, so a burst does not become a delay', async () => {
		const limit = new ConcurrencyLimit(1);
		const held = pending();
		const running = limit.run(() => held.promise);

		await expect(limit.run(async () => undefined)).rejects.toBeInstanceOf(TooBusyError);

		held.finish();
		await running;
	});

	it('releases its slot when the work fails', async () => {
		const limit = new ConcurrencyLimit(1);

		await expect(limit.run(() => Promise.reject(new Error('the hash failed')))).rejects.toThrow(
			'the hash failed'
		);

		expect(limit.inFlight).toBe(0);
	});
});

describe('the declared size of a body', () => {
	function declaring(length: number): Request {
		return new Request('http://localhost/', {
			method: 'POST',
			headers: { 'content-length': String(length) },
			body: 'x'
		});
	}

	it('accepts a form-sized submission', () => {
		expect(declaresOversizedBody(declaring(512))).toBe(false);
	});

	it('refuses one larger than the documented maximum', () => {
		expect(declaresOversizedBody(declaring(MAXIMUM_FORM_BYTES + 1))).toBe(true);
	});

	it('does not refuse a request that declares no length', () => {
		// The enforcement that cannot be lied to is the adapter's byte counter;
		// this check is only an early, cheap refusal.
		const request = new Request('http://localhost/', { method: 'POST' });

		expect(declaresOversizedBody(request)).toBe(false);
	});
});

describe('the appliance budgets', () => {
	it("are per-runtime, so one process start does not inherit another's counts", () => {
		const first = createWebLimits();
		first.logins.check('user:operator', START);

		expect(createWebLimits().logins.size).toBe(0);
	});

	it('are tighter for logins than for state changes in general', () => {
		const limits = createWebLimits();

		expect(limits.logins.limit).toBeLessThan(limits.stateChanges.limit);
	});
});

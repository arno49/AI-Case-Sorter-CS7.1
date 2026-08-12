/**
 * What an operator is told when the daemon refuses.
 *
 * The contract's error codes are machine vocabulary. These assert that each one
 * becomes a sentence about the machine the person is standing next to, and that
 * none of them leaks the daemon's own words into a page.
 */

import { describe, expect, it } from 'vitest';

import { DaemonError, safeResponseFor, type DaemonErrorCode } from './errors';

const CODES: readonly DaemonErrorCode[] = [
	'VALIDATION_FAILED',
	'UNAUTHENTICATED',
	'FORBIDDEN',
	'RESOURCE_NOT_FOUND',
	'STALE_GENERATION',
	'IDEMPOTENCY_CONFLICT',
	'NOT_READY',
	'UNCERTAIN',
	'QUEUE_FULL',
	'DEADLINE_INVALID',
	'DEADLINE_EXPIRED',
	'JOURNAL_UNAVAILABLE',
	'SERVICE_UNAVAILABLE',
	'INTERNAL_ERROR'
];

function rejected(code: DaemonErrorCode, detail = 'daemon words'): DaemonError {
	return new DaemonError({
		kind: 'rejected',
		status: 409,
		code,
		requestId: 'a5b1c2d3-0000-4000-8000-000000000000',
		detail
	});
}

describe('every code the contract can return', () => {
	it.each(CODES)('has an answer for the operator: %s', (code) => {
		const { status, message } = rejected(code).safeResponse;

		expect(status).toBeGreaterThanOrEqual(400);
		expect(message.length).toBeGreaterThan(0);
	});

	it.each(CODES)('never repeats the daemon back to the browser: %s', (code) => {
		expect(rejected(code).safeResponse.message).not.toContain('daemon words');
	});

	// `UNCERTAIN` is the exception: that word is the operator-facing vocabulary
	// the architecture requires to be prominent, not leaked wire detail.
	it.each(CODES.filter((code) => code !== 'UNCERTAIN'))(
		'never puts the code itself on the page: %s',
		(code) => {
			expect(rejected(code).safeResponse.message).not.toContain(code);
		}
	);
});

describe('the codes that matter most', () => {
	it('says an UNCERTAIN machine may have moved', () => {
		const { message } = rejected('UNCERTAIN').safeResponse;

		expect(message).toContain('UNCERTAIN');
		expect(message).toContain('may have moved');
	});

	it('does not tell an operator that their account was refused', () => {
		// A daemon 401 or 403 means this service's own credential is wrong. The
		// operator can do nothing about it and must not be sent to look at their
		// own permissions.
		for (const code of ['UNAUTHENTICATED', 'FORBIDDEN'] as const) {
			const { status, message } = rejected(code).safeResponse;

			expect(status).toBe(502);
			expect(message.toLowerCase()).not.toContain('permission');
			expect(message.toLowerCase()).not.toContain('sign in');
		}
	});

	it('tells a stale page to reload rather than blaming the operator', () => {
		expect(rejected('STALE_GENERATION').safeResponse).toMatchObject({ status: 409 });
		expect(rejected('STALE_GENERATION').safeResponse.message).toContain('Reload');
	});

	it('says new motion is blocked when the machine cannot record it', () => {
		expect(rejected('JOURNAL_UNAVAILABLE').safeResponse.message).toContain('blocked');
	});
});

describe('a failure with no code at all', () => {
	it('treats an unreachable daemon as the machine not being commanded', () => {
		const error = new DaemonError({ kind: 'unreachable', detail: 'ENOENT' });

		expect(error.safeResponse).toEqual({
			status: 503,
			message: 'The machine service is not answering. The machine was not commanded.'
		});
	});

	it('treats a timeout as unknown rather than as failed', () => {
		const { status, message } = new DaemonError({ kind: 'timeout' }).safeResponse;

		expect(status).toBe(504);
		expect(message).toContain('Check its state');
	});

	it('falls back to unavailable for a refusal it cannot read', () => {
		expect(new DaemonError({ kind: 'rejected', status: 500 }).safeResponse.status).toBe(502);
	});

	it('answers for something that is not a daemon error at all', () => {
		expect(safeResponseFor(new Error('a bug in this workspace')).status).toBe(502);
	});
});

describe('what the server keeps for itself', () => {
	it('carries the daemon code, request id and words in the error message', () => {
		const error = rejected('QUEUE_FULL', 'the admission queue is full');

		expect(error.message).toContain('QUEUE_FULL');
		expect(error.message).toContain('a5b1c2d3-0000-4000-8000-000000000000');
		expect(error.message).toContain('the admission queue is full');
	});
});

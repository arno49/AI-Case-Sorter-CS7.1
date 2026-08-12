/**
 * Forgery and cost, through the real hook and the real login action.
 *
 * A cross-site page can make a browser send a request; what it cannot do is
 * read what this appliance rendered. Every test here is written from that
 * position: the forging side may choose the method, the body and the target,
 * but never the token.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { startSession } from '$lib/server/auth/boundary';
import { CSRF_FIELD, csrfCookieName } from '$lib/server/auth/csrf';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser } from '$lib/server/auth/users';
import { LOGIN_ATTEMPT_LIMIT, MAXIMUM_FORM_BYTES, STATE_CHANGE_LIMIT } from '$lib/server/limits';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';

import {
	browser,
	csrfFor,
	raisedBy,
	request,
	throughHook as through,
	type CookieJar
} from './harness';
import { handle } from '../hooks.server';
import { actions as loginActions } from './login/+page.server';
import type { RequestEvent } from '@sveltejs/kit';

let directory: string;

const throughHook = (event: RequestEvent) => through(event, handle);

async function signedIn(): Promise<CookieJar> {
	const { config, database } = webRuntime();
	const user = await createUser(
		database,
		{ username: 'operator', password: PASSWORD, role: 'operator' },
		new Date()
	);
	const cookies = browser();
	startSession(database, cookies, user.userId, config.profile, new Date());
	return cookies;
}

/** A sign-out post from a page this appliance rendered for this browser. */
function signOut(cookies: CookieJar, form: Record<string, string> = {}): RequestEvent {
	return request('/logout', cookies, { form: { [CSRF_FIELD]: csrfFor(cookies), ...form } });
}

beforeEach(() => {
	directory = mkdtempSync(join(tmpdir(), 'cs71-csrf-'));
	process.env.CS71_WEB_PROFILE = 'development';
	process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
});

afterEach(() => {
	closeWebRuntime();
	rmSync(directory, { recursive: true, force: true });
	delete process.env.CS71_WEB_PROFILE;
	delete process.env.CS71_WEB_DATABASE_PATH;
});

describe('a state-changing request from the appliance itself', () => {
	it('is allowed when it carries the token that was issued', async () => {
		const cookies = await signedIn();

		expect('allowed' in (await throughHook(signOut(cookies)))).toBe(true);
	});

	it('is allowed when the token arrives in a header instead of a field', async () => {
		const cookies = await signedIn();
		const event = request('/logout', cookies, {
			form: {},
			headers: { 'x-csrf-token': csrfFor(cookies) }
		});

		expect('allowed' in (await throughHook(event))).toBe(true);
	});
});

describe('a state-changing request a forging page could produce', () => {
	it('is refused when it carries no token', async () => {
		const cookies = await signedIn();

		expect(await throughHook(request('/logout', cookies, { form: {} }))).toEqual({ refused: 403 });
	});

	it('is refused when it carries a guessed token', async () => {
		const cookies = await signedIn();
		const event = request('/logout', cookies, { form: { [CSRF_FIELD]: 'not-the-token' } });

		expect(await throughHook(event)).toEqual({ refused: 403 });
	});

	it('is refused when it comes from another origin, token or not', async () => {
		const cookies = await signedIn();
		const event = request('/logout', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			origin: 'https://elsewhere.example'
		});

		expect(await throughHook(event)).toEqual({ refused: 403 });
	});

	it('is refused when it names no origin at all', async () => {
		const cookies = await signedIn();
		const event = request('/logout', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			origin: null
		});

		expect(await throughHook(event)).toEqual({ refused: 403 });
	});

	it('is refused when the token belongs to a different session', async () => {
		const cookies = await signedIn();
		const { config, database } = webRuntime();
		const other = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const otherBrowser = browser();
		startSession(database, otherBrowser, other.userId, config.profile, new Date());

		expect(await throughHook(signOut(cookies, { [CSRF_FIELD]: csrfFor(otherBrowser) }))).toEqual({
			refused: 403
		});
	});
});

describe('what a request may cost', () => {
	it('refuses a body larger than the appliance accepts', async () => {
		const cookies = await signedIn();
		const event = request('/logout', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			headers: { 'content-length': String(MAXIMUM_FORM_BYTES + 1) }
		});

		expect(await throughHook(event)).toEqual({ refused: 413 });
	});

	it('refuses too many state-changing requests from one address', async () => {
		const cookies = await signedIn();
		for (let attempt = 0; attempt < STATE_CHANGE_LIMIT; attempt += 1) {
			expect('allowed' in (await throughHook(signOut(cookies)))).toBe(true);
		}

		expect(await throughHook(signOut(cookies))).toEqual({ refused: 429 });
	});

	it('leaves reading the machine unrationed and unencumbered', async () => {
		// A GET carries no Origin requirement and no token, or the dashboard
		// would be unreachable from a bookmark.
		const cookies = await signedIn();

		expect('allowed' in (await throughHook(request('/', cookies, { origin: null })))).toBe(true);
	});
});

describe('signing in', () => {
	/** The login form as the browser would submit it, after loading the page. */
	function attempt(cookies: CookieJar, password: string): RequestEvent {
		return request('/login', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies), username: 'operator', password }
		});
	}

	beforeEach(async () => {
		await createUser(
			webRuntime().database,
			{ username: 'operator', password: PASSWORD, role: 'operator' },
			new Date()
		);
	});

	it('is refused by the hook when the form carries no token', async () => {
		const cookies = browser();
		csrfFor(cookies);

		const event = request('/login', cookies, {
			form: { username: 'operator', password: PASSWORD }
		});

		expect(await throughHook(event)).toEqual({ refused: 403 });
	});

	it('is refused when the token was not the one in this browser cookie', async () => {
		const cookies = browser();
		csrfFor(cookies);
		const event = request('/login', cookies, {
			form: { [CSRF_FIELD]: csrfFor(browser()), username: 'operator', password: PASSWORD }
		});

		expect(await throughHook(event)).toEqual({ refused: 403 });
	});

	it('passes the hook and signs in when the token is the one this browser was given', async () => {
		const cookies = browser();
		const event = attempt(cookies, PASSWORD);

		expect('allowed' in (await throughHook(event))).toBe(true);

		const accepted = await raisedBy(() => loginActions.default(event));
		expect(accepted).toMatchObject({ status: 303, location: '/' });
		expect(cookies.store.has(csrfCookieName('development'))).toBe(false);
	});

	it('stops answering guesses long before a password could be found', async () => {
		const cookies = browser();
		const statuses: unknown[] = [];
		for (let guess = 0; guess <= LOGIN_ATTEMPT_LIMIT; guess += 1) {
			const result = (await loginActions.default(attempt(cookies, `guess-${guess}-is-wrong`))) as {
				status: number;
			};
			statuses.push(result.status);
		}

		// The budget is spent on wrong passwords, and then refused outright.
		expect(statuses).toEqual([...Array(LOGIN_ATTEMPT_LIMIT).fill(401), 429]);
	});

	it('lets an operator who mistyped once and then succeeded keep trying later', async () => {
		const cookies = browser();
		await loginActions.default(attempt(cookies, 'not-the-password'));

		await raisedBy(() => loginActions.default(attempt(cookies, PASSWORD)));

		// The budget was cleared by success, so a later attempt is answered.
		expect(await loginActions.default(attempt(browser(), 'not-the-password'))).toMatchObject({
			status: 401
		});
	});

	it('refuses to hash more passwords at once than the appliance can afford', async () => {
		const attempts = [browser(), browser(), browser()].map((cookies, index) =>
			loginActions.default(
				request('/login', cookies, {
					form: {
						[CSRF_FIELD]: csrfFor(cookies),
						username: `operator-${index}`,
						password: 'a-password-long-enough'
					},
					address: `10.0.0.${index + 10}`
				})
			)
		);

		const settled = await Promise.all(attempts);

		expect(
			settled.filter((result) => (result as { status: number }).status === 429).length
		).toBeGreaterThanOrEqual(1);
	});
});

/**
 * The signed-in lifecycle end to end, through the real hook and route actions.
 *
 * These exercise the wiring the unit tests deliberately do not reach: that the
 * hook denies by default, that the login action is the only thing that sets a
 * cookie, and that signing out makes the cookie the browser still holds
 * useless rather than merely absent.
 */

import { isRedirect, type Cookies, type Handle, type RequestEvent } from '@sveltejs/kit';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { sessionCookieName } from '$lib/server/auth/cookies';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser } from '$lib/server/auth/users';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';

import { handle } from '../hooks.server';
import { actions as loginActions, load as loginLoad } from './login/+page.server';
import { actions as logoutActions } from './logout/+page.server';

let directory: string;
const COOKIE = sessionCookieName('development');

/** A cookie jar shared across the requests of one simulated browser. */
function browser(): Cookies & { readonly store: Map<string, string> } {
	const store = new Map<string, string>();
	return {
		store,
		get: (name: string) => store.get(name),
		getAll: () => [...store].map(([name, value]) => ({ name, value })),
		set: (name: string, value: string) => store.set(name, value),
		delete: (name: string) => store.delete(name),
		serialize: () => ''
	} as unknown as Cookies & { readonly store: Map<string, string> };
}

function request(path: string, cookies: Cookies, form?: Record<string, string>): RequestEvent {
	return {
		cookies,
		url: new URL(`http://localhost${path}`),
		locals: {},
		request: {
			formData: async () => {
				const data = new FormData();
				for (const [key, value] of Object.entries(form ?? {})) {
					data.set(key, value);
				}
				return data;
			}
		}
	} as unknown as RequestEvent;
}

/** Run the hook, returning either the redirect it raised or the event it allowed. */
async function throughHook(
	event: RequestEvent
): Promise<{ redirectedTo: string } | { allowed: RequestEvent }> {
	const resolve = (async () => new Response('ok')) as Parameters<Handle>[0]['resolve'];
	try {
		await handle({ event, resolve });
	} catch (raised) {
		if (isRedirect(raised)) {
			return { redirectedTo: raised.location };
		}
		throw raised;
	}
	return { allowed: event };
}

/** A `load` receives more than a bare request event; the extras go unused here. */
function asLoadEvent(event: RequestEvent): Parameters<typeof loginLoad>[0] {
	return event as unknown as Parameters<typeof loginLoad>[0];
}

async function raisedBy(action: () => unknown): Promise<unknown> {
	try {
		return await action();
	} catch (raised) {
		return raised;
	}
}

beforeEach(async () => {
	directory = mkdtempSync(join(tmpdir(), 'cs71-flow-'));
	process.env.CS71_WEB_PROFILE = 'development';
	process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
	await createUser(
		webRuntime().database,
		{ username: 'operator', password: PASSWORD, role: 'operator' },
		new Date()
	);
});

afterEach(() => {
	closeWebRuntime();
	rmSync(directory, { recursive: true, force: true });
	delete process.env.CS71_WEB_PROFILE;
	delete process.env.CS71_WEB_DATABASE_PATH;
});

describe('the request hook', () => {
	it('denies an unauthenticated request to a protected page', async () => {
		expect(await throughHook(request('/', browser()))).toEqual({ redirectedTo: '/login' });
	});

	it('denies a page nobody remembered to list as public', async () => {
		expect(await throughHook(request('/operations', browser()))).toEqual({
			redirectedTo: '/login'
		});
	});

	it('lets the login page through', async () => {
		const result = await throughHook(request('/login', browser()));

		expect('allowed' in result).toBe(true);
	});

	it('reports why a presented session was refused, without a return path', async () => {
		const cookies = browser();
		cookies.set(COOKIE, 'a-token-nobody-issued', { path: '/' });

		expect(await throughHook(request('/', cookies))).toEqual({
			redirectedTo: '/login?reason=unknown'
		});
	});
});

describe('signing in and out', () => {
	it('carries an operator from the login form to a protected page and back out', async () => {
		const cookies = browser();

		// The wrong password neither authenticates nor sets a cookie.
		const refused = await loginActions.default(
			request('/login', cookies, { username: 'operator', password: 'wrong-password' })
		);
		expect(refused).toMatchObject({ status: 401 });
		expect(cookies.store.size).toBe(0);

		const accepted = await raisedBy(() =>
			loginActions.default(request('/login', cookies, { username: 'operator', password: PASSWORD }))
		);
		expect(isRedirect(accepted) && accepted.location).toBe('/');
		const issued = cookies.get(COOKIE);
		expect(issued).toBeDefined();

		// The same browser now reaches the protected page as a known operator.
		const allowed = await throughHook(request('/', cookies));
		expect('allowed' in allowed).toBe(true);
		if ('allowed' in allowed) {
			expect(allowed.allowed.locals.user?.username).toBe('operator');
			expect(allowed.allowed.locals.session).not.toBeNull();
		}

		const signedOut = await raisedBy(() => logoutActions.default(request('/logout', cookies)));
		expect(isRedirect(signedOut) && signedOut.location).toBe('/login');
		expect(cookies.get(COOKIE)).toBeUndefined();

		// A browser that kept the old cookie is told the session was revoked.
		cookies.set(COOKIE, issued ?? '', { path: '/' });
		expect(await throughHook(request('/', cookies))).toEqual({
			redirectedTo: '/login?reason=revoked'
		});
	});

	it('rotates the session, so a token planted before the login cannot survive it', async () => {
		const cookies = browser();
		await raisedBy(() =>
			loginActions.default(request('/login', cookies, { username: 'operator', password: PASSWORD }))
		);
		const first = cookies.get(COOKIE);

		await raisedBy(() =>
			loginActions.default(request('/login', cookies, { username: 'operator', password: PASSWORD }))
		);

		expect(cookies.get(COOKIE)).not.toBe(first);
		const stale = browser();
		stale.set(COOKIE, first ?? '', { path: '/' });
		expect(await throughHook(request('/', stale))).toEqual({
			redirectedTo: '/login?reason=revoked'
		});
	});

	it('refuses a login form that was not a form at all', async () => {
		const result = await loginActions.default(request('/login', browser(), {}));

		expect(result).toMatchObject({ status: 400 });
	});

	it('sends an already-signed-in browser away from the login page', async () => {
		const event = request('/login', browser());
		event.locals.user = { username: 'operator' } as never;

		const raised = await raisedBy(() => loginLoad(asLoadEvent(event)));

		expect(isRedirect(raised) && raised.location).toBe('/');
	});

	it('offers no message for an invented reason code', async () => {
		expect(await loginLoad(asLoadEvent(request('/login?reason=<script>', browser())))).toEqual({
			notice: null
		});
	});
});

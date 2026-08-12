import type { Cookies } from '@sveltejs/kit';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { authenticateRequest, endSession, startSession } from './boundary';
import { sessionCookieName, sessionCookieOptions } from './cookies';
import type { WebDatabase } from './database';
import { NOW, PASSWORD, at, memoryDatabase } from './harness';
import {
	ABSOLUTE_LIFETIME_MS,
	IDLE_TIMEOUT_MS,
	issueSession,
	resolveSession,
	revokeSession
} from './sessions';
import { createUser, setUserDisabled } from './users';

/**
 * A cookie jar with the small part of the SvelteKit interface these functions
 * use, recording what was set and deleted so the policy can be asserted.
 */
interface Jar extends Cookies {
	readonly store: Map<string, string>;
	readonly setCalls: { name: string; value: string; options: Record<string, unknown> }[];
	readonly deleted: string[];
}

function jar(initial: Record<string, string> = {}): Jar {
	const store = new Map(Object.entries(initial));
	const setCalls: Jar['setCalls'] = [];
	const deleted: string[] = [];
	return {
		store,
		setCalls,
		deleted,
		get: (name: string) => store.get(name),
		getAll: () => [...store].map(([name, value]) => ({ name, value })),
		set: (name: string, value: string, options: Record<string, unknown>) => {
			store.set(name, value);
			setCalls.push({ name, value, options });
		},
		delete: (name: string) => {
			store.delete(name);
			deleted.push(name);
		},
		serialize: () => ''
	} as unknown as Jar;
}

const PROFILE = 'production';
const COOKIE = sessionCookieName(PROFILE);

let database: WebDatabase;
let userId: string;

beforeEach(async () => {
	database = memoryDatabase();
	const user = await createUser(
		database,
		{ username: 'operator', password: PASSWORD, role: 'operator' },
		NOW
	);
	userId = user.userId;
});

afterEach(() => {
	database.close();
});

describe('authenticateRequest', () => {
	it('is unauthenticated with no reason when no cookie was presented', () => {
		const cookies = jar();

		expect(authenticateRequest(database, cookies, PROFILE, NOW)).toEqual({
			authenticated: false
		});
		expect(cookies.deleted).toEqual([]);
	});

	it('resolves a live session to its user', () => {
		const { token } = issueSession(database, { userId }, NOW);

		const result = authenticateRequest(database, jar({ [COOKIE]: token }), PROFILE, at(1000));

		expect(result.authenticated).toBe(true);
		if (result.authenticated) {
			expect(result.user.username).toBe('operator');
		}
	});

	it('clears the cookie and reports why, for every rejection', () => {
		const expired = issueSession(database, { userId }, NOW);
		const revoked = issueSession(database, { userId }, NOW);
		revokeSession(database, revoked.session.sessionId, 'administrative', at(1000));

		for (const [token, reason] of [
			[expired.token, 'expired'],
			[revoked.token, 'revoked'],
			['a-token-nobody-issued', 'unknown']
		] as const) {
			const cookies = jar({ [COOKIE]: token });

			expect(authenticateRequest(database, cookies, PROFILE, at(IDLE_TIMEOUT_MS))).toEqual({
				authenticated: false,
				rejection: reason
			});
			expect(cookies.deleted).toEqual([COOKIE]);
			expect(cookies.get(COOKIE)).toBeUndefined();
		}
	});

	it('drops an open session the moment its account is disabled', () => {
		const { token } = issueSession(database, { userId }, NOW);
		const cookies = jar({ [COOKIE]: token });

		setUserDisabled(database, { userId, disabled: true }, at(1000));

		// Disabling revokes, so the request is refused without waiting for the
		// idle window to close on an operator who no longer has access.
		expect(authenticateRequest(database, cookies, PROFILE, at(1000))).toEqual({
			authenticated: false,
			rejection: 'revoked'
		});
		expect(cookies.deleted).toEqual([COOKIE]);
	});

	it('does not read a cookie belonging to another profile', () => {
		const { token } = issueSession(database, { userId }, NOW);

		// The production name is `__Host-` prefixed; a development cookie is not
		// the same credential and must not be picked up.
		const result = authenticateRequest(
			database,
			jar({ [sessionCookieName('development')]: token }),
			PROFILE,
			NOW
		);

		expect(result).toEqual({ authenticated: false });
	});
});

describe('startSession', () => {
	it('sets the session cookie under the full policy', () => {
		const cookies = jar();

		startSession(database, cookies, userId, PROFILE, NOW);

		expect(cookies.setCalls).toHaveLength(1);
		const [call] = cookies.setCalls;
		expect(call.name).toBe('__Host-cs71_session');
		expect(call.options).toEqual(sessionCookieOptions(PROFILE));
		expect(call.options).toMatchObject({
			path: '/',
			httpOnly: true,
			sameSite: 'strict',
			secure: true
		});
	});

	it('rotates: a session the browser already held is revoked', () => {
		const planted = issueSession(database, { userId }, NOW);
		const cookies = jar({ [COOKIE]: planted.token });

		const issued = startSession(database, cookies, userId, PROFILE, at(1000));

		expect(issued.sessionId).not.toBe(planted.session.sessionId);
		expect(cookies.get(COOKIE)).not.toBe(planted.token);
		expect(resolveSession(database, planted.token, at(1000))).toEqual({
			ok: false,
			reason: 'revoked'
		});
	});

	it('does not fail when the presented cookie was already dead', () => {
		const stale = issueSession(database, { userId }, NOW);

		const issued = startSession(
			database,
			jar({ [COOKIE]: stale.token }),
			userId,
			PROFILE,
			at(ABSOLUTE_LIFETIME_MS + 1000)
		);

		expect(issued.sessionId).not.toBe(stale.session.sessionId);
	});
});

describe('endSession', () => {
	it('revokes the presented session and drops the cookie', () => {
		const { token } = issueSession(database, { userId }, NOW);
		const cookies = jar({ [COOKIE]: token });

		expect(endSession(database, cookies, PROFILE, at(1000))).toBe(true);
		expect(cookies.deleted).toEqual([COOKIE]);
		expect(resolveSession(database, token, at(1000))).toEqual({ ok: false, reason: 'revoked' });
	});

	it('still drops the cookie when there was nothing to revoke', () => {
		const cookies = jar({ [COOKIE]: 'a-token-nobody-issued' });

		expect(endSession(database, cookies, PROFILE, NOW)).toBe(false);
		expect(cookies.deleted).toEqual([COOKIE]);
	});

	it('is harmless when no cookie was presented', () => {
		expect(endSession(database, jar(), PROFILE, NOW)).toBe(false);
	});
});

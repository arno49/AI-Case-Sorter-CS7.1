/**
 * The session cookie policy.
 *
 * The cookie carries an opaque token and nothing else — no role, no user id, no
 * signature over claims — so it cannot be read or reasoned about by the browser
 * and cannot outlive the server-side session row it points at.
 *
 * Production uses the `__Host-` prefix, which browsers refuse to accept unless
 * the cookie is `Secure`, scoped to `/` and carries no `Domain`. That makes a
 * cookie planted by a neighbouring host on the same site unable to impersonate
 * ours. The prefix cannot be used over plain HTTP, so development keeps the
 * unprefixed name; the profile decides, not a caller.
 */

import type { Cookies } from '@sveltejs/kit';

import type { WebProfile } from '../config';
import { ABSOLUTE_LIFETIME_MS } from './sessions';

const BASE_NAME = 'cs71_session';
const HOST_PREFIXED_NAME = `__Host-${BASE_NAME}`;

export function sessionCookieName(profile: WebProfile): string {
	return profile === 'production' ? HOST_PREFIXED_NAME : BASE_NAME;
}

export interface SessionCookieOptions {
	readonly path: '/';
	readonly httpOnly: true;
	readonly sameSite: 'strict';
	readonly secure: boolean;
	readonly maxAge: number;
}

/**
 * `SameSite=Strict` because the appliance is a single origin with no
 * cross-site entry point worth preserving: a request that arrives from
 * anywhere else should not carry an operator's session at all.
 */
export function sessionCookieOptions(profile: WebProfile): SessionCookieOptions {
	return {
		path: '/',
		httpOnly: true,
		sameSite: 'strict',
		secure: profile === 'production',
		maxAge: Math.floor(ABSOLUTE_LIFETIME_MS / 1000)
	};
}

export function setSessionCookie(cookies: Cookies, token: string, profile: WebProfile): void {
	cookies.set(sessionCookieName(profile), token, sessionCookieOptions(profile));
}

/** Remove the cookie with the same attributes it was set with, or it survives. */
export function clearSessionCookie(cookies: Cookies, profile: WebProfile): void {
	cookies.delete(sessionCookieName(profile), { path: '/' });
}

export function readSessionCookie(cookies: Cookies, profile: WebProfile): string | undefined {
	return cookies.get(sessionCookieName(profile)) || undefined;
}

/**
 * Where a browser request becomes an authenticated actor, or does not.
 *
 * These are plain functions over a database, a cookie jar and an instant, so
 * the rules can be tested without a running SvelteKit server. `hooks.server.ts`
 * and the route actions are thin wiring on top.
 *
 * A request that presents a token the server will not honour always leaves
 * without that cookie. Carrying a dead token around invites a browser to keep
 * replaying it and makes "logged out" indistinguishable from "revoked" to the
 * person using it.
 */

import type { Cookies } from '@sveltejs/kit';

import type { WebProfile } from '../config';
import { clearSessionCookie, readSessionCookie, setSessionCookie } from './cookies';
import type { WebDatabase } from './database';
import {
	issueSession,
	resolveSession,
	revokeSessionByToken,
	type SessionRecord,
	type SessionRejection
} from './sessions';
import type { UserRecord } from './users';

export interface AuthenticatedRequest {
	readonly user: UserRecord;
	readonly session: SessionRecord;
}

export type RequestAuthentication =
	| ({ readonly authenticated: true } & AuthenticatedRequest)
	| {
			readonly authenticated: false;
			/** `undefined` when no cookie was presented at all. */
			readonly rejection?: SessionRejection;
	  };

/** Resolve the presented cookie, clearing it whenever it cannot authenticate. */
export function authenticateRequest(
	database: WebDatabase,
	cookies: Cookies,
	profile: WebProfile,
	now: Date
): RequestAuthentication {
	const token = readSessionCookie(cookies, profile);
	if (token === undefined) {
		return { authenticated: false };
	}

	const resolved = resolveSession(database, token, now);
	if (!resolved.ok) {
		clearSessionCookie(cookies, profile);
		return { authenticated: false, rejection: resolved.reason };
	}

	return { authenticated: true, user: resolved.user, session: resolved.session };
}

/**
 * Start a session for a user who has just proved their password.
 *
 * Any session the browser already held is revoked as part of issuing the new
 * one, so a token planted before the login cannot survive it.
 */
export function startSession(
	database: WebDatabase,
	cookies: Cookies,
	userId: string,
	profile: WebProfile,
	now: Date
): SessionRecord {
	const presented = readSessionCookie(cookies, profile);
	const existing = presented === undefined ? undefined : resolveSession(database, presented, now);
	const replaces = existing?.ok === true ? existing.session.sessionId : null;

	const issued = issueSession(database, { userId, replaces }, now);
	setSessionCookie(cookies, issued.token, profile);
	return issued.session;
}

/** End the presented session and drop its cookie. */
export function endSession(
	database: WebDatabase,
	cookies: Cookies,
	profile: WebProfile,
	now: Date
): boolean {
	const token = readSessionCookie(cookies, profile);
	clearSessionCookie(cookies, profile);
	if (token === undefined) {
		return false;
	}
	return revokeSessionByToken(database, token, 'logout', now);
}

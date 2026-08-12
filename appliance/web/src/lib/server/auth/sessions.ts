/**
 * Opaque server-side sessions.
 *
 * The browser holds a random token and nothing else; every fact about the
 * session — who it belongs to, when it expires, whether it was revoked — lives
 * in `web.db` and is re-read on each request. Revocation is therefore immediate
 * and complete, which a signed self-describing cookie cannot offer.
 *
 * Two clocks bound a session. The idle window slides while it is in use, so an
 * unattended browser stops being an authenticated one; the absolute lifetime
 * does not slide, so no session can be kept alive indefinitely by activity.
 *
 * Logging in issues a new session and revokes the one it replaces, rather than
 * re-pointing an existing row: a token an attacker planted before the login
 * must not survive it.
 */

import type { WebDatabase } from './database';
import { createIdentifier, createToken, hashToken } from './tokens';
import { findUserById, type UserRecord } from './users';

export const IDLE_TIMEOUT_MS = 30 * 60 * 1000;
export const ABSOLUTE_LIFETIME_MS = 12 * 60 * 60 * 1000;

export type RevocationReason =
	'logout' | 'rotated' | 'password_changed' | 'user_disabled' | 'administrative';

export interface SessionRecord {
	readonly sessionId: string;
	readonly userId: string;
	readonly createdAt: string;
	readonly lastSeenAt: string;
	readonly idleExpiresAt: string;
	readonly absoluteExpiresAt: string;
	readonly revokedAt: string | null;
	readonly revokedReason: string | null;
}

export interface IssuedSession {
	/** The only time the token exists outside the browser. Never persisted. */
	readonly token: string;
	readonly session: SessionRecord;
}

export type SessionRejection = 'unknown' | 'revoked' | 'expired' | 'user_disabled';

export type ResolvedSession =
	| { readonly ok: true; readonly session: SessionRecord; readonly user: UserRecord }
	| { readonly ok: false; readonly reason: SessionRejection };

interface SessionRow {
	session_id: string;
	user_id: string;
	created_at: string;
	last_seen_at: string;
	idle_expires_at: string;
	absolute_expires_at: string;
	revoked_at: string | null;
	revoked_reason: string | null;
}

const SESSION_COLUMNS =
	'session_id, user_id, created_at, last_seen_at, idle_expires_at,' +
	' absolute_expires_at, revoked_at, revoked_reason';

export interface IssueSessionOptions {
	readonly userId: string;
	/** The session this one supersedes; revoked in the same transaction. */
	readonly replaces?: string | null;
}

export function issueSession(
	database: WebDatabase,
	options: IssueSessionOptions,
	now: Date
): IssuedSession {
	const token = createToken();
	const sessionId = createIdentifier('sess');
	const issue = database.transaction((): SessionRecord => {
		if (options.replaces) {
			revokeSession(database, options.replaces, 'rotated', now);
		}
		database
			.prepare(
				'INSERT INTO sessions (session_id, token_hash, user_id, created_at, last_seen_at,' +
					' idle_expires_at, absolute_expires_at, revoked_at, revoked_reason)' +
					' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'
			)
			.run(
				sessionId,
				hashToken(token),
				options.userId,
				now.toISOString(),
				now.toISOString(),
				new Date(now.getTime() + IDLE_TIMEOUT_MS).toISOString(),
				new Date(now.getTime() + ABSOLUTE_LIFETIME_MS).toISOString(),
				null,
				null
			);
		const stored = readSessionById(database, sessionId);
		if (stored === undefined) {
			throw new Error('the session that was just written could not be read back');
		}
		return stored;
	});

	return { token, session: issue.immediate() };
}

/**
 * Resolve a presented token to its session and user, sliding the idle window.
 *
 * Every rejection reason is reported to the caller rather than collapsed into
 * "not logged in", so the interface can tell an operator that their session
 * expired instead of implying they typed a password wrong.
 */
export function resolveSession(database: WebDatabase, token: string, now: Date): ResolvedSession {
	const row = database
		.prepare<[string], SessionRow>(`SELECT ${SESSION_COLUMNS} FROM sessions WHERE token_hash = ?`)
		.get(hashToken(token));
	if (row === undefined) {
		return { ok: false, reason: 'unknown' };
	}

	const session = toRecord(row);
	if (session.revokedAt !== null) {
		return { ok: false, reason: 'revoked' };
	}
	if (hasExpired(session, now)) {
		return { ok: false, reason: 'expired' };
	}

	const user = findUserById(database, session.userId);
	if (user === undefined) {
		return { ok: false, reason: 'unknown' };
	}
	if (user.disabledAt !== null) {
		return { ok: false, reason: 'user_disabled' };
	}

	return { ok: true, session: slideIdleWindow(database, session, now), user };
}

export function revokeSession(
	database: WebDatabase,
	sessionId: string,
	reason: RevocationReason,
	now: Date
): boolean {
	const result = database
		.prepare(
			'UPDATE sessions SET revoked_at = ?, revoked_reason = ?' +
				' WHERE session_id = ? AND revoked_at IS NULL'
		)
		.run(now.toISOString(), reason, sessionId);
	return result.changes > 0;
}

export function revokeSessionByToken(
	database: WebDatabase,
	token: string,
	reason: RevocationReason,
	now: Date
): boolean {
	const result = database
		.prepare(
			'UPDATE sessions SET revoked_at = ?, revoked_reason = ?' +
				' WHERE token_hash = ? AND revoked_at IS NULL'
		)
		.run(now.toISOString(), reason, hashToken(token));
	return result.changes > 0;
}

/** Revoke every live session of one user. Used by disable and password change. */
export function revokeSessionsForUser(
	database: WebDatabase,
	userId: string,
	reason: RevocationReason,
	now: Date
): number {
	const result = database
		.prepare(
			'UPDATE sessions SET revoked_at = ?, revoked_reason = ?' +
				' WHERE user_id = ? AND revoked_at IS NULL'
		)
		.run(now.toISOString(), reason, userId);
	return result.changes;
}

export function activeSessions(
	database: WebDatabase,
	userId: string,
	now: Date
): readonly SessionRecord[] {
	return database
		.prepare<[string], SessionRow>(
			`SELECT ${SESSION_COLUMNS} FROM sessions WHERE user_id = ? AND revoked_at IS NULL`
		)
		.all(userId)
		.map(toRecord)
		.filter((session) => !hasExpired(session, now));
}

/**
 * Delete sessions that can no longer authenticate anything.
 *
 * Only rows past their absolute lifetime are removed. A revoked but unexpired
 * session is retained so that presenting its token is still answered with
 * `revoked` rather than `unknown`.
 */
export function pruneExpiredSessions(database: WebDatabase, now: Date): number {
	return database
		.prepare('DELETE FROM sessions WHERE absolute_expires_at <= ?')
		.run(now.toISOString()).changes;
}

function slideIdleWindow(database: WebDatabase, session: SessionRecord, now: Date): SessionRecord {
	// The idle window may extend up to, but never past, the absolute lifetime.
	const proposed = new Date(now.getTime() + IDLE_TIMEOUT_MS);
	const absolute = new Date(session.absoluteExpiresAt);
	const idleExpiresAt = (proposed < absolute ? proposed : absolute).toISOString();
	const lastSeenAt = now.toISOString();
	database
		.prepare('UPDATE sessions SET last_seen_at = ?, idle_expires_at = ? WHERE session_id = ?')
		.run(lastSeenAt, idleExpiresAt, session.sessionId);
	return { ...session, lastSeenAt, idleExpiresAt };
}

function hasExpired(session: SessionRecord, now: Date): boolean {
	const at = now.toISOString();
	return session.idleExpiresAt <= at || session.absoluteExpiresAt <= at;
}

function readSessionById(database: WebDatabase, sessionId: string): SessionRecord | undefined {
	const row = database
		.prepare<[string], SessionRow>(`SELECT ${SESSION_COLUMNS} FROM sessions WHERE session_id = ?`)
		.get(sessionId);
	return row === undefined ? undefined : toRecord(row);
}

function toRecord(row: SessionRow): SessionRecord {
	return {
		sessionId: row.session_id,
		userId: row.user_id,
		createdAt: row.created_at,
		lastSeenAt: row.last_seen_at,
		idleExpiresAt: row.idle_expires_at,
		absoluteExpiresAt: row.absolute_expires_at,
		revokedAt: row.revoked_at,
		revokedReason: row.revoked_reason
	};
}

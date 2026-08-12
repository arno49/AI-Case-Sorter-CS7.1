import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { WebDatabase } from './database';
import { NOW, PASSWORD, at, memoryDatabase } from './harness';
import {
	ABSOLUTE_LIFETIME_MS,
	IDLE_TIMEOUT_MS,
	activeSessions,
	issueSession,
	pruneExpiredSessions,
	resolveSession,
	revokeSession,
	revokeSessionByToken,
	revokeSessionsForUser
} from './sessions';
import { hashToken } from './tokens';
import { createUser, setUserDisabled } from './users';

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

/** Use the session often enough that only the absolute lifetime can end it. */
function keepWarmUntil(token: string, elapsedMs: number): void {
	for (let elapsed = 0; elapsed < elapsedMs; elapsed += IDLE_TIMEOUT_MS / 2) {
		resolveSession(database, token, at(elapsed));
	}
}

describe('issueSession', () => {
	it('returns a token that is stored only as a digest', () => {
		const { token, session } = issueSession(database, { userId }, NOW);

		const row = database
			.prepare<[string], { token_hash: string }>(
				'SELECT token_hash FROM sessions WHERE session_id = ?'
			)
			.get(session.sessionId);
		expect(row?.token_hash).toBe(hashToken(token));
		expect(row?.token_hash).not.toBe(token);
		expect(
			database
				.prepare<[string], { count: number }>(
					'SELECT count(*) AS count FROM sessions WHERE token_hash = ?'
				)
				.get(token)?.count
		).toBe(0);
	});

	it('bounds every session by both an idle window and an absolute lifetime', () => {
		const { session } = issueSession(database, { userId }, NOW);

		expect(session.idleExpiresAt).toBe(at(IDLE_TIMEOUT_MS).toISOString());
		expect(session.absoluteExpiresAt).toBe(at(ABSOLUTE_LIFETIME_MS).toISOString());
		expect(session.revokedAt).toBeNull();
	});

	it('issues a distinct token and identifier each time', () => {
		const first = issueSession(database, { userId }, NOW);
		const second = issueSession(database, { userId }, NOW);

		expect(second.token).not.toBe(first.token);
		expect(second.session.sessionId).not.toBe(first.session.sessionId);
	});

	it('rotates: the session it replaces is revoked in the same write', () => {
		const before = issueSession(database, { userId }, NOW);

		const after = issueSession(database, { userId, replaces: before.session.sessionId }, at(1000));

		expect(resolveSession(database, before.token, at(1000))).toEqual({
			ok: false,
			reason: 'revoked'
		});
		expect(resolveSession(database, after.token, at(1000))).toMatchObject({ ok: true });
		expect(
			database
				.prepare<[string], { revoked_reason: string }>(
					'SELECT revoked_reason FROM sessions WHERE session_id = ?'
				)
				.get(before.session.sessionId)?.revoked_reason
		).toBe('rotated');
	});

	it('refuses a session for an account that does not exist', () => {
		expect(() => issueSession(database, { userId: 'user_absent' }, NOW)).toThrow(
			'FOREIGN KEY constraint failed'
		);
	});
});

describe('resolveSession', () => {
	it('returns the session and its user', () => {
		const { token, session } = issueSession(database, { userId }, NOW);

		const resolved = resolveSession(database, token, at(1000));

		expect(resolved.ok).toBe(true);
		if (resolved.ok) {
			expect(resolved.session.sessionId).toBe(session.sessionId);
			expect(resolved.user.username).toBe('operator');
		}
	});

	it('rejects a token nobody issued', () => {
		expect(resolveSession(database, 'not-a-token', NOW)).toEqual({ ok: false, reason: 'unknown' });
	});

	it('slides the idle window while the session is in use', () => {
		const { token } = issueSession(database, { userId }, NOW);

		const resolved = resolveSession(database, token, at(IDLE_TIMEOUT_MS - 1000));

		expect(resolved.ok).toBe(true);
		if (resolved.ok) {
			expect(resolved.session.lastSeenAt).toBe(at(IDLE_TIMEOUT_MS - 1000).toISOString());
			expect(resolved.session.idleExpiresAt).toBe(at(IDLE_TIMEOUT_MS * 2 - 1000).toISOString());
		}
	});

	it('never slides the idle window past the absolute lifetime', () => {
		const { token } = issueSession(database, { userId }, NOW);
		keepWarmUntil(token, ABSOLUTE_LIFETIME_MS - 60_000);

		const resolved = resolveSession(database, token, at(ABSOLUTE_LIFETIME_MS - 60_000));

		expect(resolved.ok).toBe(true);
		if (resolved.ok) {
			// A full idle window from here would outlive the session, so it is clamped.
			expect(resolved.session.idleExpiresAt).toBe(at(ABSOLUTE_LIFETIME_MS).toISOString());
		}
	});

	it('expires an unused session at its idle window', () => {
		const { token } = issueSession(database, { userId }, NOW);

		expect(resolveSession(database, token, at(IDLE_TIMEOUT_MS))).toEqual({
			ok: false,
			reason: 'expired'
		});
	});

	it('expires a continuously used session at its absolute lifetime', () => {
		const { token } = issueSession(database, { userId }, NOW);

		// Keep it warm right up to the absolute limit, then step past it.
		for (let elapsed = IDLE_TIMEOUT_MS / 2; elapsed < ABSOLUTE_LIFETIME_MS; elapsed += 60_000) {
			expect(resolveSession(database, token, at(elapsed)).ok).toBe(true);
		}

		expect(resolveSession(database, token, at(ABSOLUTE_LIFETIME_MS))).toEqual({
			ok: false,
			reason: 'expired'
		});
	});

	it('rejects a session whose account was disabled', () => {
		const { token } = issueSession(database, { userId }, NOW);
		// Revocation is what disabling normally does; force the account disabled
		// without it to prove the resolve path checks the account too.
		database
			.prepare('UPDATE users SET disabled_at = ? WHERE user_id = ?')
			.run(at(1000).toISOString(), userId);

		expect(resolveSession(database, token, at(1000))).toEqual({
			ok: false,
			reason: 'user_disabled'
		});
	});
});

describe('revocation', () => {
	it('revokes by session identifier and reports whether anything changed', () => {
		const { token, session } = issueSession(database, { userId }, NOW);

		expect(revokeSession(database, session.sessionId, 'logout', at(1000))).toBe(true);
		expect(revokeSession(database, session.sessionId, 'logout', at(2000))).toBe(false);
		expect(resolveSession(database, token, at(2000))).toEqual({ ok: false, reason: 'revoked' });
	});

	it('revokes by presented token, which is what logging out has', () => {
		const { token } = issueSession(database, { userId }, NOW);

		expect(revokeSessionByToken(database, token, 'logout', at(1000))).toBe(true);
		expect(revokeSessionByToken(database, 'not-a-token', 'logout', at(1000))).toBe(false);
		expect(resolveSession(database, token, at(1000))).toEqual({ ok: false, reason: 'revoked' });
	});

	it('revokes every live session of one account at once', () => {
		const first = issueSession(database, { userId }, NOW);
		const second = issueSession(database, { userId }, NOW);

		expect(revokeSessionsForUser(database, userId, 'administrative', at(1000))).toBe(2);
		expect(revokeSessionsForUser(database, userId, 'administrative', at(2000))).toBe(0);
		expect(resolveSession(database, first.token, at(1000)).ok).toBe(false);
		expect(resolveSession(database, second.token, at(1000)).ok).toBe(false);
	});

	it('is final: the storage engine refuses to restore a revoked session', () => {
		const { session } = issueSession(database, { userId }, NOW);
		revokeSession(database, session.sessionId, 'logout', at(1000));

		expect(() =>
			database
				.prepare('UPDATE sessions SET revoked_at = NULL WHERE session_id = ?')
				.run(session.sessionId)
		).toThrow('a revoked session cannot be restored');
	});

	it('is final: a session cannot be rebound to another account or token', async () => {
		const { session } = issueSession(database, { userId }, NOW);
		const other = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			NOW
		);

		expect(() =>
			database
				.prepare('UPDATE sessions SET user_id = ? WHERE session_id = ?')
				.run(other.userId, session.sessionId)
		).toThrow('a session cannot be rebound');
		expect(() =>
			database
				.prepare('UPDATE sessions SET token_hash = ? WHERE session_id = ?')
				.run(hashToken('planted'), session.sessionId)
		).toThrow('a session cannot be rebound');
	});
});

describe('activeSessions', () => {
	it('counts only sessions that could still authenticate a request', () => {
		const live = issueSession(database, { userId }, NOW);
		const revoked = issueSession(database, { userId }, NOW);
		revokeSession(database, revoked.session.sessionId, 'logout', at(1000));

		expect(activeSessions(database, userId, at(1000)).map((s) => s.sessionId)).toEqual([
			live.session.sessionId
		]);
		expect(activeSessions(database, userId, at(IDLE_TIMEOUT_MS))).toEqual([]);
	});

	it('is emptied when the account is disabled', () => {
		issueSession(database, { userId }, NOW);

		setUserDisabled(database, { userId, disabled: true }, at(1000));

		expect(activeSessions(database, userId, at(1000))).toEqual([]);
	});
});

describe('pruneExpiredSessions', () => {
	it('removes only sessions past their absolute lifetime', () => {
		const { token } = issueSession(database, { userId }, NOW);

		expect(pruneExpiredSessions(database, at(IDLE_TIMEOUT_MS))).toBe(0);
		// Still answerable as expired rather than unknown while it is retained.
		expect(resolveSession(database, token, at(IDLE_TIMEOUT_MS))).toEqual({
			ok: false,
			reason: 'expired'
		});

		expect(pruneExpiredSessions(database, at(ABSOLUTE_LIFETIME_MS))).toBe(1);
		expect(resolveSession(database, token, at(ABSOLUTE_LIFETIME_MS))).toEqual({
			ok: false,
			reason: 'unknown'
		});
	});
});

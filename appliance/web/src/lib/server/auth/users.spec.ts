import { hash as argon2Hash, type Algorithm, type Version } from '@node-rs/argon2';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { WebDatabase } from './database';
import { NOW, PASSWORD, at, memoryDatabase } from './harness';
import { WeakPasswordError, needsRehash, verifyPassword } from './passwords';
import { activeSessions, issueSession, resolveSession } from './sessions';
import {
	DuplicateUsernameError,
	InvalidRoleError,
	InvalidUsernameError,
	UnknownUserError,
	authenticate,
	changePassword,
	createUser,
	findUserById,
	findUserByUsername,
	listUsers,
	normalizeUsername,
	setUserDisabled,
	userCount,
	type Role
} from './users';

let database: WebDatabase;

beforeEach(() => {
	database = memoryDatabase();
});

afterEach(() => {
	database.close();
});

async function anOperator(username = 'operator'): Promise<string> {
	const user = await createUser(database, { username, password: PASSWORD, role: 'operator' }, NOW);
	return user.userId;
}

/**
 * A valid Argon2id hash produced below the current policy, standing in for one
 * written by an earlier release. Only a test may reach for the hashing library
 * directly; production code goes through `passwords.ts`.
 */
async function weakerHash(password: string): Promise<string> {
	return argon2Hash(password, {
		algorithm: 2 as Algorithm,
		version: 1 as Version,
		memoryCost: 19_456,
		timeCost: 2,
		parallelism: 1,
		outputLen: 32
	});
}

function storedHash(userId: string): string {
	return (
		database
			.prepare<[string], { password_hash: string }>(
				'SELECT password_hash FROM users WHERE user_id = ?'
			)
			.get(userId)?.password_hash ?? ''
	);
}

describe('a fresh database', () => {
	it('has no account, so no password is a usable default', async () => {
		expect(userCount(database)).toBe(0);
		expect(listUsers(database)).toEqual([]);

		for (const candidate of ['admin', 'root', 'cs71', 'operator']) {
			const outcome = await authenticate(database, { username: candidate, password: 'admin' });
			expect(outcome).toEqual({ ok: false, reason: 'unknown_user' });
		}
	});
});

describe('createUser', () => {
	it('stores an Argon2id hash and never the password', async () => {
		const user = await createUser(
			database,
			{ username: 'Operator', password: PASSWORD, role: 'operator' },
			NOW
		);

		expect(user.username).toBe('operator');
		expect(user.role).toBe('operator');
		expect(user.disabledAt).toBeNull();
		const stored = database
			.prepare<[string], { password_hash: string }>(
				'SELECT password_hash FROM users WHERE user_id = ?'
			)
			.get(user.userId);
		expect(stored?.password_hash.startsWith('$argon2id$')).toBe(true);
		expect(await verifyPassword(stored?.password_hash ?? '', PASSWORD)).toBe(true);
	});

	it('refuses a duplicate username regardless of case', async () => {
		await anOperator('sorter');

		await expect(
			createUser(database, { username: 'SORTER', password: PASSWORD, role: 'viewer' }, NOW)
		).rejects.toThrow(DuplicateUsernameError);
		expect(userCount(database)).toBe(1);
	});

	it('refuses a weak password before writing anything', async () => {
		await expect(
			createUser(database, { username: 'operator', password: 'short', role: 'operator' }, NOW)
		).rejects.toThrow(WeakPasswordError);
		expect(userCount(database)).toBe(0);
	});

	it('refuses a role outside the documented matrix', async () => {
		await expect(
			createUser(
				database,
				{ username: 'operator', password: PASSWORD, role: 'superuser' as Role },
				NOW
			)
		).rejects.toThrow(InvalidRoleError);
	});
});

describe('normalizeUsername', () => {
	it('folds case and surrounding whitespace', () => {
		expect(normalizeUsername('  Operator  ')).toBe('operator');
	});

	it('rejects names that are too short, too long or oddly shaped', () => {
		for (const candidate of ['ab', 'a'.repeat(33), '.leading', 'has space', 'Ünicode', '']) {
			expect(() => normalizeUsername(candidate)).toThrow(InvalidUsernameError);
		}
	});
});

describe('authenticate', () => {
	it('accepts the right password', async () => {
		const userId = await anOperator();

		const outcome = await authenticate(database, { username: 'operator', password: PASSWORD });

		expect(outcome).toEqual({ ok: true, user: findUserById(database, userId) });
	});

	it('separates a wrong password from an unknown account', async () => {
		await anOperator();

		expect(
			await authenticate(database, { username: 'operator', password: 'wrong-password!' })
		).toEqual({ ok: false, reason: 'invalid_password' });
		expect(await authenticate(database, { username: 'nobody', password: PASSWORD })).toEqual({
			ok: false,
			reason: 'unknown_user'
		});
	});

	it('treats a malformed username as an unknown account rather than an error', async () => {
		expect(await authenticate(database, { username: 'a b c', password: PASSWORD })).toEqual({
			ok: false,
			reason: 'unknown_user'
		});
	});

	it('refuses a disabled account even with the right password', async () => {
		const userId = await anOperator();
		setUserDisabled(database, { userId, disabled: true }, NOW);

		expect(await authenticate(database, { username: 'operator', password: PASSWORD })).toEqual({
			ok: false,
			reason: 'disabled'
		});
	});

	it('upgrades a hash stored below the current policy on a successful login', async () => {
		const userId = await anOperator();
		const legacy = await weakerHash(PASSWORD);
		database.prepare('UPDATE users SET password_hash = ? WHERE user_id = ?').run(legacy, userId);

		expect(
			await authenticate(database, { username: 'operator', password: PASSWORD })
		).toMatchObject({ ok: true });

		const upgraded = storedHash(userId);
		expect(upgraded).not.toBe(legacy);
		expect(needsRehash(upgraded)).toBe(false);
		expect(await verifyPassword(upgraded, PASSWORD)).toBe(true);
	});

	it('leaves a below-policy hash alone when the password was wrong', async () => {
		const userId = await anOperator();
		const legacy = await weakerHash(PASSWORD);
		database.prepare('UPDATE users SET password_hash = ? WHERE user_id = ?').run(legacy, userId);

		expect(
			await authenticate(database, { username: 'operator', password: 'not-the-password' })
		).toEqual({ ok: false, reason: 'invalid_password' });
		expect(storedHash(userId)).toBe(legacy);
	});
});

describe('changePassword', () => {
	it('replaces the hash and revokes every session it had authorized', async () => {
		const userId = await anOperator();
		const first = issueSession(database, { userId }, NOW);
		const second = issueSession(database, { userId }, NOW);

		await changePassword(database, { userId, password: 'a-new-and-longer-password' }, at(1000));

		expect(await authenticate(database, { username: 'operator', password: PASSWORD })).toEqual({
			ok: false,
			reason: 'invalid_password'
		});
		expect(
			await authenticate(database, { username: 'operator', password: 'a-new-and-longer-password' })
		).toMatchObject({ ok: true });
		expect(resolveSession(database, first.token, at(1000))).toEqual({
			ok: false,
			reason: 'revoked'
		});
		expect(resolveSession(database, second.token, at(1000))).toEqual({
			ok: false,
			reason: 'revoked'
		});
		expect(activeSessions(database, userId, at(1000))).toEqual([]);
	});

	it('records when the password changed', async () => {
		const userId = await anOperator();

		const updated = await changePassword(
			database,
			{ userId, password: 'a-new-and-longer-password' },
			at(5000)
		);

		expect(updated.passwordChangedAt).toBe(at(5000).toISOString());
		expect(updated.createdAt).toBe(NOW.toISOString());
	});

	it('rejects a weak replacement without touching the stored hash', async () => {
		const userId = await anOperator();

		await expect(changePassword(database, { userId, password: 'short' }, NOW)).rejects.toThrow(
			WeakPasswordError
		);
		expect(
			await authenticate(database, { username: 'operator', password: PASSWORD })
		).toMatchObject({ ok: true });
	});

	it('reports an unknown account', async () => {
		await expect(
			changePassword(database, { userId: 'user_absent', password: PASSWORD }, NOW)
		).rejects.toThrow(UnknownUserError);
	});
});

describe('setUserDisabled', () => {
	it('revokes live sessions when the account is disabled', async () => {
		const userId = await anOperator();
		const session = issueSession(database, { userId }, NOW);

		const disabled = setUserDisabled(database, { userId, disabled: true }, at(2000));

		expect(disabled.disabledAt).toBe(at(2000).toISOString());
		expect(resolveSession(database, session.token, at(2000))).toEqual({
			ok: false,
			reason: 'revoked'
		});
	});

	it('does not resurrect revoked sessions when the account is re-enabled', async () => {
		const userId = await anOperator();
		const session = issueSession(database, { userId }, NOW);
		setUserDisabled(database, { userId, disabled: true }, at(2000));

		const enabled = setUserDisabled(database, { userId, disabled: false }, at(3000));

		expect(enabled.disabledAt).toBeNull();
		expect(resolveSession(database, session.token, at(3000))).toEqual({
			ok: false,
			reason: 'revoked'
		});
	});

	it('reports an unknown account', async () => {
		expect(() => setUserDisabled(database, { userId: 'user_absent', disabled: true }, NOW)).toThrow(
			UnknownUserError
		);
	});
});

describe('lookups', () => {
	it('finds an account by id and by username, and nothing otherwise', async () => {
		const userId = await anOperator();

		expect(findUserById(database, userId)?.username).toBe('operator');
		expect(findUserByUsername(database, 'operator')?.userId).toBe(userId);
		expect(findUserById(database, 'user_absent')).toBeUndefined();
		expect(findUserByUsername(database, 'absent')).toBeUndefined();
	});

	it('lists accounts by username', async () => {
		await anOperator('zoe');
		await anOperator('adam');

		expect(listUsers(database).map((user) => user.username)).toEqual(['adam', 'zoe']);
	});
});

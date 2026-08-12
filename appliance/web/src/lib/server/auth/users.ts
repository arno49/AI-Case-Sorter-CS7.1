/**
 * Local accounts and the operations that must invalidate what they authorized.
 *
 * There is no default account and no default password: a fresh `web.db` has an
 * empty `users` table, so every credential that exists was created through
 * bootstrap provisioning or by an administrator.
 *
 * Disabling an account and changing a password both revoke that user's live
 * sessions in the same transaction as the change. If the write that revokes
 * fails, the change that made it necessary is rolled back too, so a password is
 * never quietly changed while the old sessions keep working.
 */

import type { WebDatabase } from './database';
import {
	assertUsablePassword,
	hashPassword,
	needsRehash,
	spendVerificationEffort,
	verifyPassword
} from './passwords';
import { revokeSessionsForUser } from './sessions';
import { createIdentifier } from './tokens';

export const ROLES = ['viewer', 'operator', 'administrator'] as const;

export type Role = (typeof ROLES)[number];

export const MINIMUM_USERNAME_LENGTH = 3;
export const MAXIMUM_USERNAME_LENGTH = 32;

const USERNAME_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;

export class InvalidUsernameError extends Error {}

export class DuplicateUsernameError extends Error {}

export class UnknownUserError extends Error {}

export class InvalidRoleError extends Error {}

export interface UserRecord {
	readonly userId: string;
	readonly username: string;
	readonly role: Role;
	readonly createdAt: string;
	readonly passwordChangedAt: string;
	readonly disabledAt: string | null;
}

export type AuthenticationOutcome =
	| { readonly ok: true; readonly user: UserRecord }
	| { readonly ok: false; readonly reason: 'unknown_user' | 'disabled' | 'invalid_password' };

interface UserRow {
	user_id: string;
	username: string;
	password_hash: string;
	role: Role;
	created_at: string;
	password_changed_at: string;
	disabled_at: string | null;
}

const USER_COLUMNS =
	'user_id, username, password_hash, role, created_at, password_changed_at, disabled_at';

export interface CreateUserOptions {
	readonly username: string;
	readonly password: string;
	readonly role: Role;
}

/** A validated, already-hashed account, ready to be inserted inside a transaction. */
export interface PreparedUser {
	readonly userId: string;
	readonly username: string;
	readonly passwordHash: string;
	readonly role: Role;
}

/**
 * Validate and hash an account without touching the database.
 *
 * Argon2id is deliberately slow, so it must not run while a write transaction
 * is open. Splitting preparation from insertion also lets a caller that needs
 * the insertion to be atomic with other writes — bootstrap provisioning — do
 * the expensive part first.
 */
export async function prepareUser(options: CreateUserOptions): Promise<PreparedUser> {
	const username = normalizeUsername(options.username);
	assertUsablePassword(options.password);
	if (!ROLES.includes(options.role)) {
		throw new InvalidRoleError(`unknown role ${options.role}`);
	}
	return {
		userId: createIdentifier('user'),
		username,
		passwordHash: await hashPassword(options.password),
		role: options.role
	};
}

export function insertUser(database: WebDatabase, prepared: PreparedUser, now: Date): UserRecord {
	const timestamp = now.toISOString();
	try {
		database
			.prepare(`INSERT INTO users (${USER_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?)`)
			.run(
				prepared.userId,
				prepared.username,
				prepared.passwordHash,
				prepared.role,
				timestamp,
				timestamp,
				null
			);
	} catch (cause) {
		if (isUniqueViolation(cause)) {
			throw new DuplicateUsernameError(`the username ${prepared.username} is already taken`);
		}
		throw cause;
	}
	return requireUser(database, prepared.userId);
}

export async function createUser(
	database: WebDatabase,
	options: CreateUserOptions,
	now: Date
): Promise<UserRecord> {
	return insertUser(database, await prepareUser(options), now);
}

export function findUserById(database: WebDatabase, userId: string): UserRecord | undefined {
	const row = database
		.prepare<[string], UserRow>(`SELECT ${USER_COLUMNS} FROM users WHERE user_id = ?`)
		.get(userId);
	return row === undefined ? undefined : toRecord(row);
}

export function findUserByUsername(
	database: WebDatabase,
	username: string
): UserRecord | undefined {
	const row = readRowByUsername(database, username);
	return row === undefined ? undefined : toRecord(row);
}

export function listUsers(database: WebDatabase): readonly UserRecord[] {
	return database
		.prepare<[], UserRow>(`SELECT ${USER_COLUMNS} FROM users ORDER BY username`)
		.all()
		.map(toRecord);
}

export function userCount(database: WebDatabase): number {
	const row = database.prepare<[], { count: number }>('SELECT count(*) AS count FROM users').get();
	return row?.count ?? 0;
}

/**
 * Check a username and password.
 *
 * An unknown or disabled account still pays the full hashing cost, so the
 * response time does not reveal which usernames exist. A successful login
 * against a hash produced below the current policy is re-hashed in place.
 */
export async function authenticate(
	database: WebDatabase,
	credentials: { readonly username: string; readonly password: string }
): Promise<AuthenticationOutcome> {
	let username: string;
	try {
		username = normalizeUsername(credentials.username);
	} catch {
		await spendVerificationEffort(credentials.password);
		return { ok: false, reason: 'unknown_user' };
	}

	const row = readRowByUsername(database, username);
	if (row === undefined) {
		await spendVerificationEffort(credentials.password);
		return { ok: false, reason: 'unknown_user' };
	}
	if (row.disabled_at !== null) {
		await spendVerificationEffort(credentials.password);
		return { ok: false, reason: 'disabled' };
	}
	if (!(await verifyPassword(row.password_hash, credentials.password))) {
		return { ok: false, reason: 'invalid_password' };
	}

	if (needsRehash(row.password_hash)) {
		const upgraded = await hashPassword(credentials.password);
		database
			.prepare('UPDATE users SET password_hash = ? WHERE user_id = ?')
			.run(upgraded, row.user_id);
	}

	return { ok: true, user: requireUser(database, row.user_id) };
}

/** Change a password and revoke every session it had already authorized. */
export async function changePassword(
	database: WebDatabase,
	options: { readonly userId: string; readonly password: string },
	now: Date
): Promise<UserRecord> {
	assertUsablePassword(options.password);
	const passwordHash = await hashPassword(options.password);
	const apply = database.transaction(() => {
		const result = database
			.prepare('UPDATE users SET password_hash = ?, password_changed_at = ? WHERE user_id = ?')
			.run(passwordHash, now.toISOString(), options.userId);
		if (result.changes === 0) {
			throw new UnknownUserError(`no user ${options.userId}`);
		}
		revokeSessionsForUser(database, options.userId, 'password_changed', now);
	});
	apply.immediate();
	return requireUser(database, options.userId);
}

/**
 * Disable or re-enable an account.
 *
 * Disabling revokes live sessions rather than relying on the next request to
 * notice: a disabled operator must lose an open dashboard, not keep it until
 * their idle window closes.
 */
export function setUserDisabled(
	database: WebDatabase,
	options: { readonly userId: string; readonly disabled: boolean },
	now: Date
): UserRecord {
	const apply = database.transaction(() => {
		const result = database
			.prepare('UPDATE users SET disabled_at = ? WHERE user_id = ?')
			.run(options.disabled ? now.toISOString() : null, options.userId);
		if (result.changes === 0) {
			throw new UnknownUserError(`no user ${options.userId}`);
		}
		if (options.disabled) {
			revokeSessionsForUser(database, options.userId, 'user_disabled', now);
		}
	});
	apply.immediate();
	return requireUser(database, options.userId);
}

export function normalizeUsername(username: string): string {
	const normalized = username.normalize('NFKC').trim().toLowerCase();
	if (
		normalized.length < MINIMUM_USERNAME_LENGTH ||
		normalized.length > MAXIMUM_USERNAME_LENGTH ||
		!USERNAME_PATTERN.test(normalized)
	) {
		throw new InvalidUsernameError(
			`a username must be ${MINIMUM_USERNAME_LENGTH}-${MAXIMUM_USERNAME_LENGTH} characters of` +
				' lowercase letters, digits, dot, underscore or hyphen, starting with a letter or digit'
		);
	}
	return normalized;
}

function readRowByUsername(database: WebDatabase, username: string): UserRow | undefined {
	return database
		.prepare<[string], UserRow>(`SELECT ${USER_COLUMNS} FROM users WHERE username = ?`)
		.get(username);
}

function requireUser(database: WebDatabase, userId: string): UserRecord {
	const user = findUserById(database, userId);
	if (user === undefined) {
		throw new UnknownUserError(`no user ${userId}`);
	}
	return user;
}

function toRecord(row: UserRow): UserRecord {
	return {
		userId: row.user_id,
		username: row.username,
		role: row.role,
		createdAt: row.created_at,
		passwordChangedAt: row.password_changed_at,
		disabledAt: row.disabled_at
	};
}

function isUniqueViolation(cause: unknown): boolean {
	return cause instanceof Error && cause.message.includes('UNIQUE constraint failed');
}

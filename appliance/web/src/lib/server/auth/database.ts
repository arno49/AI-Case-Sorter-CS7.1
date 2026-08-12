/**
 * The web service's own SQLite database.
 *
 * SvelteKit exclusively owns `web.db`, as `cs71d` exclusively owns `machine.db`;
 * neither process reads the other's database as a shortcut. This module is the
 * only place that may import `better-sqlite3` or write SQL, so the file's
 * ownership, permissions and schema have exactly one enforcement point.
 *
 * Migrations are forward-only and checksummed. A database written by a newer or
 * altered schema refuses to open rather than being downgraded in place, because
 * a half-understood accounts table is worse than a service that will not start.
 *
 * Several invariants are enforced by the storage engine rather than by
 * convention, so that a bug in application code cannot quietly violate them:
 * a stored password must be an Argon2id encoding, a revoked session can never
 * become live again, a session can never be re-bound to another user or token,
 * a bootstrap token can be claimed at most once, and provisioning cannot be
 * un-done to re-open the one-time bootstrap window.
 */

import Database from 'better-sqlite3';
import { createHash } from 'node:crypto';
import { chmodSync, existsSync, mkdirSync, statSync } from 'node:fs';
import { dirname } from 'node:path';

export type WebDatabase = Database.Database;

export const IN_MEMORY = ':memory:';

/** Owner-only. Any other user with read access can lift every session row. */
const REQUIRED_FILE_MODE = 0o600;
const OTHER_ACCESS_MASK = 0o077;

export class WebDatabaseError extends Error {}

export class WebSchemaError extends WebDatabaseError {}

export interface Migration {
	readonly version: number;
	readonly name: string;
	readonly statements: readonly string[];
}

export const MIGRATIONS: readonly Migration[] = [
	{
		version: 1,
		name: 'accounts_sessions_and_provisioning',
		statements: [
			`CREATE TABLE users (
				user_id TEXT PRIMARY KEY,
				username TEXT NOT NULL UNIQUE,
				password_hash TEXT NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
				role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'administrator')),
				created_at TEXT NOT NULL,
				password_changed_at TEXT NOT NULL,
				disabled_at TEXT
			)`,
			`CREATE TABLE sessions (
				session_id TEXT PRIMARY KEY,
				token_hash TEXT NOT NULL UNIQUE,
				user_id TEXT NOT NULL REFERENCES users(user_id),
				created_at TEXT NOT NULL,
				last_seen_at TEXT NOT NULL,
				idle_expires_at TEXT NOT NULL,
				absolute_expires_at TEXT NOT NULL,
				revoked_at TEXT,
				revoked_reason TEXT
			)`,
			'CREATE INDEX sessions_by_user ON sessions(user_id)',
			'CREATE INDEX sessions_by_absolute_expiry ON sessions(absolute_expires_at)',
			`CREATE TABLE bootstrap_tokens (
				token_hash TEXT PRIMARY KEY,
				issued_at TEXT NOT NULL,
				expires_at TEXT NOT NULL,
				claimed_at TEXT,
				claimed_user_id TEXT REFERENCES users(user_id)
			)`,
			`CREATE TABLE provisioning_state (
				id INTEGER PRIMARY KEY CHECK (id = 1),
				initialized_at TEXT,
				version INTEGER NOT NULL
			)`,
			'INSERT INTO provisioning_state (id, initialized_at, version) VALUES (1, NULL, 1)',
			`CREATE TRIGGER sessions_revocation_is_final
			BEFORE UPDATE ON sessions
			WHEN OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS NULL
			BEGIN
				SELECT RAISE(ABORT, 'a revoked session cannot be restored');
			END`,
			`CREATE TRIGGER sessions_cannot_be_rebound
			BEFORE UPDATE ON sessions
			WHEN NEW.user_id <> OLD.user_id OR NEW.token_hash <> OLD.token_hash
			BEGIN
				SELECT RAISE(ABORT, 'a session cannot be rebound; issue a new one');
			END`,
			`CREATE TRIGGER bootstrap_tokens_are_single_use
			BEFORE UPDATE ON bootstrap_tokens
			WHEN OLD.claimed_at IS NOT NULL
			BEGIN
				SELECT RAISE(ABORT, 'a bootstrap token can be claimed only once');
			END`,
			`CREATE TRIGGER provisioning_cannot_be_undone
			BEFORE UPDATE ON provisioning_state
			WHEN OLD.initialized_at IS NOT NULL AND NEW.initialized_at IS NULL
			BEGIN
				SELECT RAISE(ABORT, 'provisioning cannot be re-opened in place');
			END`
		]
	},
	{
		version: 2,
		name: 'web_audit',
		statements: [
			// The web audit is attribution, not the machine journal of record:
			// `cs71d` owns what the machine did in its own database, and these rows
			// say who asked for it from a browser. `operation_id` is the join key
			// between the two, and there is no transaction across them.
			`CREATE TABLE web_audit (
				audit_id TEXT PRIMARY KEY,
				occurred_at TEXT NOT NULL,
				user_id TEXT NOT NULL,
				role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'administrator')),
				action TEXT NOT NULL,
				outcome TEXT NOT NULL CHECK (outcome IN ('accepted', 'refused', 'failed')),
				request_id TEXT NOT NULL,
				operation_id TEXT,
				daemon_code TEXT,
				daemon_request_id TEXT
			)`,
			// No foreign key to `users`: an audit entry has to outlive the account
			// it describes, and a deleted account must not take its history away.
			'CREATE INDEX web_audit_by_time ON web_audit(occurred_at)',
			'CREATE INDEX web_audit_by_operation ON web_audit(operation_id)',
			`CREATE TRIGGER web_audit_is_append_only
			BEFORE UPDATE ON web_audit
			BEGIN
				SELECT RAISE(ABORT, 'an audit entry cannot be edited');
			END`
		]
	}
];

export const SCHEMA_VERSION = MIGRATIONS[MIGRATIONS.length - 1].version;

const CREATE_MIGRATION_LEDGER = `
	CREATE TABLE IF NOT EXISTS schema_migrations (
		version INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		applied_at TEXT NOT NULL,
		checksum TEXT NOT NULL
	)
`;

export function migrationChecksum(migration: Migration): string {
	return createHash('sha256').update(migration.statements.join('\n')).digest('hex');
}

export interface OpenOptions {
	/** Clock for the migration ledger; tests pass a fixed instant. */
	readonly now?: () => Date;
	/**
	 * Create the parent directory when it is missing. Production directories,
	 * their ownership and their modes belong to the installer, so this stays
	 * opt-in and is used by development and tests only.
	 */
	readonly createDirectory?: boolean;
}

/** Open or create `web.db`, enforce its permissions and apply migrations. */
export function openWebDatabase(path: string, options: OpenOptions = {}): WebDatabase {
	const now = options.now ?? (() => new Date());
	const inMemory = path === IN_MEMORY;

	if (!inMemory) {
		if (options.createDirectory) {
			mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
		}
		assertNotReachableByOthers(path);
	}

	let database: WebDatabase;
	try {
		database = new Database(path);
	} catch (cause) {
		throw new WebDatabaseError(`cannot open ${path}: ${describe(cause)}`, { cause });
	}

	try {
		if (!inMemory) {
			// Before WAL, so the -wal and -shm files inherit owner-only access.
			chmodSync(path, REQUIRED_FILE_MODE);
			database.pragma('journal_mode = WAL');
		}
		database.pragma('synchronous = FULL');
		database.pragma('foreign_keys = ON');
		migrate(database, now);
	} catch (cause) {
		database.close();
		throw cause;
	}

	return database;
}

/**
 * Refuse a database another local identity can already reach.
 *
 * A file created before this check with a permissive umask is repaired rather
 * than refused; one that is group- or world-accessible when we did not create
 * it may already have been read, and silently tightening the mode would hide
 * that.
 */
function assertNotReachableByOthers(path: string): void {
	if (!existsSync(path)) {
		return;
	}
	const mode = statSync(path).mode & 0o777;
	if ((mode & OTHER_ACCESS_MASK) !== 0) {
		throw new WebDatabaseError(
			`${path} is accessible to other users (mode ${mode.toString(8)}); ` +
				'web.db must be exclusive to the web service identity'
		);
	}
}

function migrate(database: WebDatabase, now: () => Date): void {
	database.exec(CREATE_MIGRATION_LEDGER);
	const applied = database
		.prepare<[], { version: number; name: string; checksum: string }>(
			'SELECT version, name, checksum FROM schema_migrations ORDER BY version'
		)
		.all();

	if (applied.length > MIGRATIONS.length) {
		throw new WebSchemaError(
			`web.db is at schema version ${applied[applied.length - 1].version}, ` +
				`newer than the supported ${SCHEMA_VERSION}; this build refuses to downgrade it`
		);
	}

	for (const [index, record] of applied.entries()) {
		const expected = MIGRATIONS[index];
		if (record.version !== expected.version || record.checksum !== migrationChecksum(expected)) {
			throw new WebSchemaError(
				`web.db migration ${record.version} (${record.name}) does not match this build; ` +
					'the schema has diverged and will not be reconciled automatically'
			);
		}
	}

	const applyPending = database.transaction(() => {
		for (const migration of MIGRATIONS.slice(applied.length)) {
			for (const statement of migration.statements) {
				database.exec(statement);
			}
			database
				.prepare(
					'INSERT INTO schema_migrations (version, name, applied_at, checksum)' +
						' VALUES (?, ?, ?, ?)'
				)
				.run(migration.version, migration.name, now().toISOString(), migrationChecksum(migration));
		}
	});
	applyPending.immediate();
}

export function schemaVersion(database: WebDatabase): number {
	const row = database
		.prepare<[], { version: number | null }>(
			'SELECT max(version) AS version FROM schema_migrations'
		)
		.get();
	return row?.version ?? 0;
}

function describe(cause: unknown): string {
	return cause instanceof Error ? cause.message : String(cause);
}

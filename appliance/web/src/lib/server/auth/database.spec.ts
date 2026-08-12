import { chmodSync, statSync, writeFileSync } from 'node:fs';
import { afterEach, describe, expect, it } from 'vitest';

import {
	MIGRATIONS,
	SCHEMA_VERSION,
	WebDatabaseError,
	WebSchemaError,
	migrationChecksum,
	openWebDatabase,
	schemaVersion
} from './database';
import { NOW, fileDatabase, memoryDatabase, type TemporaryDatabase } from './harness';

const opened: TemporaryDatabase[] = [];

function temporary(): TemporaryDatabase {
	const handle = fileDatabase();
	opened.push(handle);
	return handle;
}

afterEach(() => {
	while (opened.length > 0) {
		opened.pop()?.close();
	}
});

describe('openWebDatabase', () => {
	it('applies every migration and records its checksum', () => {
		const database = memoryDatabase();

		expect(schemaVersion(database)).toBe(SCHEMA_VERSION);
		const ledger = database
			.prepare<[], { version: number; checksum: string }>(
				'SELECT version, checksum FROM schema_migrations ORDER BY version'
			)
			.all();
		expect(ledger).toEqual(
			MIGRATIONS.map((migration) => ({
				version: migration.version,
				checksum: migrationChecksum(migration)
			}))
		);
		database.close();
	});

	it('is idempotent across restarts', () => {
		const handle = temporary();
		handle.database.close();

		const reopened = openWebDatabase(handle.path, { now: () => NOW });
		expect(schemaVersion(reopened)).toBe(SCHEMA_VERSION);
		expect(
			reopened
				.prepare<[], { count: number }>('SELECT count(*) AS count FROM schema_migrations')
				.get()
		).toEqual({ count: MIGRATIONS.length });
		reopened.close();
	});

	it('creates the database owner-only', () => {
		const handle = temporary();

		expect(statSync(handle.path).mode & 0o777).toBe(0o600);
	});

	it('refuses a database other local users can already read', () => {
		const handle = temporary();
		handle.database.close();
		chmodSync(handle.path, 0o644);

		expect(() => openWebDatabase(handle.path)).toThrow(WebDatabaseError);
		expect(() => openWebDatabase(handle.path)).toThrow('exclusive to the web service identity');
	});

	it('refuses a schema newer than this build', () => {
		const handle = temporary();
		handle.database
			.prepare(
				'INSERT INTO schema_migrations (version, name, applied_at, checksum)' +
					" VALUES (?, 'from_the_future', ?, 'unknown')"
			)
			.run(SCHEMA_VERSION + 1, NOW.toISOString());
		handle.database.close();

		expect(() => openWebDatabase(handle.path)).toThrow(WebSchemaError);
		expect(() => openWebDatabase(handle.path)).toThrow('refuses to downgrade');
	});

	it('refuses a migration whose recorded checksum has diverged', () => {
		const handle = temporary();
		handle.database
			.prepare('UPDATE schema_migrations SET checksum = ? WHERE version = 1')
			.run('tampered');
		handle.database.close();

		expect(() => openWebDatabase(handle.path)).toThrow('the schema has diverged');
	});

	it('reports a path it cannot open rather than leaving a partial database', () => {
		expect(() => openWebDatabase('/nonexistent-directory/web.db')).toThrow(WebDatabaseError);
	});

	it('creates its parent directory only when asked', () => {
		const handle = temporary();
		const nested = `${handle.directory}/nested/web.db`;

		expect(() => openWebDatabase(nested)).toThrow(WebDatabaseError);

		const created = openWebDatabase(nested, { createDirectory: true });
		expect(statSync(nested).mode & 0o777).toBe(0o600);
		created.close();
	});

	it('refuses a file that is not a database at all', () => {
		const handle = temporary();
		const decoy = `${handle.directory}/not-a-database.db`;
		writeFileSync(decoy, 'this is not SQLite', { mode: 0o600 });

		expect(() => openWebDatabase(decoy)).toThrow();
	});
});

describe('storage-enforced invariants', () => {
	it('rejects a password hash that is not an Argon2id encoding', () => {
		const database = memoryDatabase();

		expect(() =>
			database
				.prepare(
					'INSERT INTO users (user_id, username, password_hash, role, created_at,' +
						' password_changed_at, disabled_at) VALUES (?, ?, ?, ?, ?, ?, NULL)'
				)
				.run('user_1', 'operator', 'plaintext', 'operator', NOW.toISOString(), NOW.toISOString())
		).toThrow('CHECK constraint failed');
		database.close();
	});

	it('rejects an unknown role', () => {
		const database = memoryDatabase();

		expect(() =>
			database
				.prepare(
					'INSERT INTO users (user_id, username, password_hash, role, created_at,' +
						' password_changed_at, disabled_at) VALUES (?, ?, ?, ?, ?, ?, NULL)'
				)
				.run(
					'user_1',
					'operator',
					'$argon2id$v=19$m=65536,t=3,p=1$abc$def',
					'superuser',
					NOW.toISOString(),
					NOW.toISOString()
				)
		).toThrow('CHECK constraint failed');
		database.close();
	});

	it('keeps provisioning a one-row fact', () => {
		const database = memoryDatabase();

		expect(() =>
			database.prepare('INSERT INTO provisioning_state (id, version) VALUES (2, 1)').run()
		).toThrow('CHECK constraint failed');
		database.close();
	});
});

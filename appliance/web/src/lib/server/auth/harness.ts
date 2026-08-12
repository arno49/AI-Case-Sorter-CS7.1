/**
 * Test-only helpers for the authentication suite.
 *
 * Kept beside the code it exercises rather than in a fixture directory so the
 * clock a test reasons about is visible next to the lifetimes it is checking.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { IN_MEMORY, openWebDatabase, type WebDatabase } from './database';

/** A fixed instant, so every expiry assertion is arithmetic rather than a race. */
export const NOW = new Date('2026-08-11T12:00:00.000Z');

export function at(offsetMs: number): Date {
	return new Date(NOW.getTime() + offsetMs);
}

export function memoryDatabase(): WebDatabase {
	return openWebDatabase(IN_MEMORY, { now: () => NOW });
}

export interface TemporaryDatabase {
	readonly path: string;
	readonly database: WebDatabase;
	readonly directory: string;
	close(): void;
}

export function fileDatabase(): TemporaryDatabase {
	const directory = mkdtempSync(join(tmpdir(), 'cs71-web-'));
	const path = join(directory, 'web.db');
	const database = openWebDatabase(path, { now: () => NOW });
	return {
		path,
		database,
		directory,
		close() {
			database.close();
			rmSync(directory, { recursive: true, force: true });
		}
	};
}

/**
 * A password that is long enough for the policy and distinctive enough to be
 * searched for in raw database bytes.
 */
export const PASSWORD = 'correct-horse-battery-staple-8842';

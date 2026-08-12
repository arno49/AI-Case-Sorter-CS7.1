/**
 * The bootstrap-token CLI, run as the real process it is.
 *
 * `issueBootstrapToken` itself is already covered by `provisioning.spec.ts`.
 * What only this file can prove is that the *entry point* wires it up
 * correctly: the right database path, a clean single-line message rather than
 * a stack trace when something is wrong, the token alone on stdout, and the
 * exit code an installer script would branch on.
 */

import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { openWebDatabase } from '../src/lib/server/auth/database';
import { claimBootstrapToken } from '../src/lib/server/auth/provisioning';
import { PASSWORD } from '../src/lib/server/auth/harness';

const SCRIPT = new URL('./issue-bootstrap-token.ts', import.meta.url).pathname;
const TOKEN_PATTERN = /^[A-Za-z0-9_-]{32,}$/;

let directory: string;
let databasePath: string;

beforeEach(() => {
	directory = mkdtempSync(join(tmpdir(), 'cs71-bootstrap-cli-'));
	databasePath = join(directory, 'web.db');
});

afterEach(() => {
	rmSync(directory, { recursive: true, force: true });
});

function run(env: Record<string, string> = {}): { status: number; stdout: string; stderr: string } {
	const result = spawnSync('node', ['--import', 'tsx', SCRIPT], {
		env: { ...process.env, CS71_WEB_DATABASE_PATH: databasePath, ...env },
		encoding: 'utf8'
	});
	return { status: result.status ?? -1, stdout: result.stdout, stderr: result.stderr };
}

describe('issuing a bootstrap token', () => {
	it('prints only the token on stdout, everything else on stderr', () => {
		const result = run();

		expect(result.status).toBe(0);
		expect(result.stdout.trim()).toMatch(TOKEN_PATTERN);
		expect(result.stderr).toContain('Expires');
	});

	it('creates the database on the path this workspace already owns and validates', () => {
		run();

		const database = openWebDatabase(databasePath);
		try {
			expect(database.prepare('SELECT COUNT(*) AS n FROM bootstrap_tokens').get()).toEqual({
				n: 1
			});
		} finally {
			database.close();
		}
	});

	it('issues a token that actually claims an administrator', async () => {
		const result = run();
		const database = openWebDatabase(databasePath);
		try {
			const outcome = await claimBootstrapToken(
				database,
				{ token: result.stdout.trim(), username: 'ada', password: PASSWORD },
				new Date()
			);
			expect(outcome).toMatchObject({ ok: true, user: { role: 'administrator' } });
		} finally {
			database.close();
		}
	});

	it('refuses with a clean message, not a stack trace, once provisioned', async () => {
		run();
		const database = openWebDatabase(databasePath);
		const first = run();
		await claimBootstrapToken(
			database,
			{ token: first.stdout.trim(), username: 'ada', password: PASSWORD },
			new Date()
		);
		database.close();

		const result = run();

		expect(result.status).toBe(1);
		expect(result.stdout).toBe('');
		expect(result.stderr).toContain('already provisioned');
		expect(result.stderr).not.toContain('    at ');
	});

	it('fails cleanly rather than with a stack trace when the directory does not exist', () => {
		const result = run({ CS71_WEB_DATABASE_PATH: join(directory, 'missing', 'web.db') });

		expect(result.status).toBe(1);
		expect(result.stdout).toBe('');
		expect(result.stderr).toContain('cannot open');
		expect(result.stderr).not.toContain('    at ');
	});
});

/**
 * Reading the credential this service presents to the daemon.
 */

import { chmodSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ServiceCredentialError, readServiceToken } from './credentials';

let directory: string;

function tokenFile(contents: string, mode = 0o600): string {
	const path = join(directory, 'service-token');
	writeFileSync(path, contents);
	chmodSync(path, mode);
	return path;
}

beforeEach(() => {
	directory = mkdtempSync(join(tmpdir(), 'cs71-token-'));
});

afterEach(() => {
	rmSync(directory, { recursive: true, force: true });
});

describe('the daemon service token', () => {
	it('is read from the file, without the newline an editor leaves', () => {
		expect(readServiceToken(tokenFile('a-local-service-credential\n'))).toBe(
			'a-local-service-credential'
		);
	});

	it('is refused when another user can read it', () => {
		// Repairing the mode would hide that the credential may already have
		// been copied.
		expect(() => readServiceToken(tokenFile('a-credential', 0o644))).toThrow(
			ServiceCredentialError
		);
	});

	it('is refused when the group can read it, not only the world', () => {
		expect(() => readServiceToken(tokenFile('a-credential', 0o640))).toThrow(
			ServiceCredentialError
		);
	});

	it('is refused when the file is empty', () => {
		expect(() => readServiceToken(tokenFile('   \n'))).toThrow(ServiceCredentialError);
	});

	it('is refused when the file is not there', () => {
		expect(() => readServiceToken(join(directory, 'absent'))).toThrow(ServiceCredentialError);
	});

	it('names the path but never the contents when it refuses', () => {
		const path = tokenFile('the-secret-value', 0o644);

		expect(() => readServiceToken(path)).toThrow(new RegExp(path));
		try {
			readServiceToken(path);
		} catch (raised) {
			expect((raised as Error).message).not.toContain('the-secret-value');
		}
	});
});

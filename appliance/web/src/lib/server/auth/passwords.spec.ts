import { describe, expect, it } from 'vitest';

import {
	MAXIMUM_PASSWORD_LENGTH,
	MINIMUM_PASSWORD_LENGTH,
	PASSWORD_HASH_POLICY,
	WeakPasswordError,
	assertUsablePassword,
	hashPassword,
	needsRehash,
	spendVerificationEffort,
	verifyPassword
} from './passwords';
import { PASSWORD } from './harness';

describe('hashPassword', () => {
	it('produces an Argon2id encoding carrying the policy parameters', async () => {
		const encoded = await hashPassword(PASSWORD);

		expect(encoded.startsWith('$argon2id$v=19$')).toBe(true);
		expect(encoded).toContain(`m=${PASSWORD_HASH_POLICY.memoryCost}`);
		expect(encoded).toContain(`t=${PASSWORD_HASH_POLICY.timeCost}`);
		expect(encoded).toContain(`p=${PASSWORD_HASH_POLICY.parallelism}`);
	});

	it('never contains the password it was derived from', async () => {
		const encoded = await hashPassword(PASSWORD);

		expect(encoded).not.toContain(PASSWORD);
	});

	it('salts each hash, so equal passwords do not collide', async () => {
		const [first, second] = await Promise.all([hashPassword(PASSWORD), hashPassword(PASSWORD)]);

		expect(first).not.toBe(second);
		expect(await verifyPassword(first, PASSWORD)).toBe(true);
		expect(await verifyPassword(second, PASSWORD)).toBe(true);
	});
});

describe('verifyPassword', () => {
	it('accepts the password and rejects anything else', async () => {
		const encoded = await hashPassword(PASSWORD);

		expect(await verifyPassword(encoded, PASSWORD)).toBe(true);
		expect(await verifyPassword(encoded, `${PASSWORD}!`)).toBe(false);
		expect(await verifyPassword(encoded, '')).toBe(false);
	});

	it('treats a canonically equivalent password as the same password', async () => {
		// U+00E9 versus "e" plus a combining acute accent.
		const encoded = await hashPassword('passé-partout-2026');

		expect(await verifyPassword(encoded, 'passé-partout-2026')).toBe(true);
	});

	it('fails a stored value it cannot parse instead of throwing', async () => {
		expect(await verifyPassword('not-a-hash', PASSWORD)).toBe(false);
		expect(await verifyPassword('', PASSWORD)).toBe(false);
	});

	it('refuses an oversized candidate rather than hashing it', async () => {
		const encoded = await hashPassword(PASSWORD);

		expect(await verifyPassword(encoded, 'a'.repeat(MAXIMUM_PASSWORD_LENGTH + 1))).toBe(false);
	});
});

describe('assertUsablePassword', () => {
	it('requires a minimum length', () => {
		expect(() => assertUsablePassword('a'.repeat(MINIMUM_PASSWORD_LENGTH - 1))).toThrow(
			WeakPasswordError
		);
		expect(() => assertUsablePassword('a'.repeat(MINIMUM_PASSWORD_LENGTH))).not.toThrow();
	});

	it('bounds the work an attacker can ask the server to do', () => {
		expect(() => assertUsablePassword('a'.repeat(MAXIMUM_PASSWORD_LENGTH + 1))).toThrow('at most');
	});

	it('rejects whitespace standing in for a password', () => {
		expect(() => assertUsablePassword(' '.repeat(MINIMUM_PASSWORD_LENGTH + 2))).toThrow(
			'only whitespace'
		);
	});
});

describe('needsRehash', () => {
	it('accepts a hash produced under the current policy', async () => {
		expect(needsRehash(await hashPassword(PASSWORD))).toBe(false);
	});

	it('flags weaker parameters, another algorithm and anything unparseable', () => {
		expect(needsRehash('$argon2id$v=19$m=4096,t=3,p=1$c2FsdA$aGFzaA')).toBe(true);
		expect(needsRehash('$argon2id$v=19$m=65536,t=1,p=1$c2FsdA$aGFzaA')).toBe(true);
		expect(needsRehash('$argon2i$v=19$m=65536,t=3,p=1$c2FsdA$aGFzaA')).toBe(true);
		expect(needsRehash('$argon2id$v=16$m=65536,t=3,p=1$c2FsdA$aGFzaA')).toBe(true);
		expect(needsRehash('$2b$12$abcdefghijklmnopqrstuv')).toBe(true);
	});
});

describe('spendVerificationEffort', () => {
	it('completes without revealing anything about a candidate', async () => {
		await expect(spendVerificationEffort(PASSWORD)).resolves.toBeUndefined();
	});
});

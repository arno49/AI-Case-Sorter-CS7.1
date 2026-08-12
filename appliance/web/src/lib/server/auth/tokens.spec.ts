import { describe, expect, it } from 'vitest';

import { TOKEN_BYTES, createIdentifier, createToken, digestsMatch, hashToken } from './tokens';

describe('createToken', () => {
	it('carries the full entropy and survives a cookie unencoded', () => {
		const token = createToken();

		expect(Buffer.from(token, 'base64url')).toHaveLength(TOKEN_BYTES);
		expect(token).toMatch(/^[A-Za-z0-9_-]+$/);
		expect(encodeURIComponent(token)).toBe(token);
	});

	it('does not repeat', () => {
		const tokens = new Set(Array.from({ length: 512 }, () => createToken()));

		expect(tokens.size).toBe(512);
	});
});

describe('hashToken', () => {
	it('is deterministic and never contains the token', () => {
		const token = createToken();

		expect(hashToken(token)).toBe(hashToken(token));
		expect(hashToken(token)).toMatch(/^[0-9a-f]{64}$/);
		expect(hashToken(token)).not.toContain(token);
	});

	it('separates different tokens', () => {
		expect(hashToken(createToken())).not.toBe(hashToken(createToken()));
	});
});

describe('createIdentifier', () => {
	it('is prefixed, unique and free of token material', () => {
		const first = createIdentifier('sess');
		const second = createIdentifier('sess');

		expect(first).toMatch(/^sess_[0-9a-f]{32}$/);
		expect(first).not.toBe(second);
	});
});

describe('digestsMatch', () => {
	it('compares equal-length digests without throwing on a mismatch', () => {
		const digest = hashToken(createToken());

		expect(digestsMatch(digest, digest)).toBe(true);
		expect(digestsMatch(digest, hashToken(createToken()))).toBe(false);
		expect(digestsMatch(digest, 'short')).toBe(false);
		expect(digestsMatch('', '')).toBe(true);
	});
});

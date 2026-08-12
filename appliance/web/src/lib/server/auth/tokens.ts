/**
 * Opaque credentials and the identifiers that are safe to write down.
 *
 * Session and bootstrap tokens are 256 bits of CSPRNG output, so they are not
 * guessable and carry no user data: the browser is never handed a signed
 * payload it could be tricked into replaying elsewhere.
 *
 * Only a token's SHA-256 digest is stored. That is deliberately a fast hash and
 * not Argon2id — the input is already high-entropy random, so there is no
 * dictionary to slow down, while lookup has to be one indexed query on every
 * request. A stolen database therefore yields no usable session token.
 *
 * Identifiers are distinct from tokens: `session_id` may appear in an audit
 * record, `token_hash` may not.
 */

import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';

export const TOKEN_BYTES = 32;
const IDENTIFIER_BYTES = 16;

/** A fresh opaque credential, URL-safe so it survives a cookie unencoded. */
export function createToken(): string {
	return randomBytes(TOKEN_BYTES).toString('base64url');
}

export function hashToken(token: string): string {
	return createHash('sha256').update(token, 'utf8').digest('hex');
}

/** A non-secret identifier for attribution and logs. */
export function createIdentifier(prefix: string): string {
	return `${prefix}_${randomBytes(IDENTIFIER_BYTES).toString('hex')}`;
}

/** Compare two digests without leaking their divergence point through timing. */
export function digestsMatch(left: string, right: string): boolean {
	const a = Buffer.from(left, 'utf8');
	const b = Buffer.from(right, 'utf8');
	if (a.length !== b.length) {
		return false;
	}
	return timingSafeEqual(a, b);
}

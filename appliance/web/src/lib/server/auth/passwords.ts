/**
 * Argon2id password hashing policy.
 *
 * This is the only module that may import `@node-rs/argon2`, so the parameters
 * a stored hash was produced with are decided in one place and can be raised
 * without hunting for call sites.
 *
 * Passwords are the one secret a local operator can be tricked into reusing
 * from elsewhere, so the cost is deliberately above the OWASP minimum rather
 * than at it. The chosen parameters were measured at roughly 100 ms per hash on
 * an arm64 development machine; the appliance's own cost is unmeasured until
 * the parameters are timed on representative Raspberry Pi hardware.
 */

import { hash, verify, type Algorithm, type Version } from '@node-rs/argon2';

/**
 * `Algorithm.Argon2id` and `Version.V0x13`.
 *
 * The library declares both as ambient `const enum`s, which this project's
 * `verbatimModuleSyntax` forbids reading as values. The numbers are part of the
 * Argon2 encoding rather than a library detail, and the suite asserts that a
 * produced hash actually announces `$argon2id$v=19$`, so a drift would fail a
 * test rather than silently weaken a stored password.
 */
const ARGON2ID = 2 as Algorithm;
const ARGON2_VERSION_0X13 = 1 as Version;

export const PASSWORD_HASH_POLICY = Object.freeze({
	algorithm: ARGON2ID,
	version: ARGON2_VERSION_0X13,
	/** 64 MiB per hash. */
	memoryCost: 65_536,
	timeCost: 3,
	parallelism: 1,
	outputLen: 32
});

export const MINIMUM_PASSWORD_LENGTH = 12;
/**
 * A memory-hard hash turns an unbounded password into unbounded work, so the
 * upper bound is a denial-of-service control, not a strength opinion.
 */
export const MAXIMUM_PASSWORD_LENGTH = 128;

export class WeakPasswordError extends Error {}

/** Reject a password before it reaches the hash, with a reason a user can act on. */
export function assertUsablePassword(password: string): void {
	const normalized = normalize(password);
	if (normalized.length < MINIMUM_PASSWORD_LENGTH) {
		throw new WeakPasswordError(
			`a password must be at least ${MINIMUM_PASSWORD_LENGTH} characters`
		);
	}
	if (normalized.length > MAXIMUM_PASSWORD_LENGTH) {
		throw new WeakPasswordError(`a password must be at most ${MAXIMUM_PASSWORD_LENGTH} characters`);
	}
	if (normalized.trim().length === 0) {
		throw new WeakPasswordError('a password must not be only whitespace');
	}
}

export async function hashPassword(password: string): Promise<string> {
	assertUsablePassword(password);
	return hash(normalize(password), PASSWORD_HASH_POLICY);
}

/**
 * Verify a candidate password, returning `false` rather than throwing for any
 * malformed or foreign encoding: a stored hash we cannot parse is a failed
 * login, not a server error that would distinguish one account from another.
 */
export async function verifyPassword(encoded: string, password: string): Promise<boolean> {
	if (normalize(password).length > MAXIMUM_PASSWORD_LENGTH) {
		return false;
	}
	try {
		return await verify(encoded, normalize(password));
	} catch {
		return false;
	}
}

/** True when a stored hash was produced below the current policy. */
export function needsRehash(encoded: string): boolean {
	const parsed = parseEncoding(encoded);
	if (parsed === undefined) {
		return true;
	}
	return (
		parsed.algorithm !== 'argon2id' ||
		parsed.version !== 19 ||
		parsed.memoryCost < PASSWORD_HASH_POLICY.memoryCost ||
		parsed.timeCost < PASSWORD_HASH_POLICY.timeCost ||
		parsed.parallelism !== PASSWORD_HASH_POLICY.parallelism
	);
}

let decoyHash: Promise<string> | undefined;

/**
 * Spend the same hashing effort as a real verification.
 *
 * An unknown or disabled account must not answer faster than a wrong password,
 * or the login form becomes a username oracle. The decoy hash is computed once
 * per process against an unguessable value and never matches anything.
 */
export async function spendVerificationEffort(password: string): Promise<void> {
	decoyHash ??= hash(`decoy:${crypto.randomUUID()}`, PASSWORD_HASH_POLICY);
	await verifyPassword(await decoyHash, password);
}

interface ParsedEncoding {
	readonly algorithm: string;
	readonly version: number;
	readonly memoryCost: number;
	readonly timeCost: number;
	readonly parallelism: number;
}

function parseEncoding(encoded: string): ParsedEncoding | undefined {
	const match = /^\$(argon2[a-z]+)\$v=(\d+)\$m=(\d+),t=(\d+),p=(\d+)\$/.exec(encoded);
	if (match === null) {
		return undefined;
	}
	return {
		algorithm: match[1],
		version: Number(match[2]),
		memoryCost: Number(match[3]),
		timeCost: Number(match[4]),
		parallelism: Number(match[5])
	};
}

/**
 * Normalize so a password typed with a different but canonically equivalent
 * key sequence still verifies. Hashing and verification must agree, so this is
 * applied on both paths and nowhere else.
 */
function normalize(password: string): string {
	return password.normalize('NFKC');
}

/**
 * One-time, expiry-bound bootstrap of the first administrator.
 *
 * No default password ships. A fresh appliance has no account at all, so the
 * login form cannot be answered until someone with local access to the device
 * issues a bootstrap token and uses it once, before it expires.
 *
 * Issuing a token supersedes any outstanding one, so at most a single bootstrap
 * credential is ever live. The window closes permanently once claimed: further
 * accounts are created by the administrator, not by re-running provisioning.
 *
 * The token is compared in constant time against the single outstanding record
 * rather than looked up by digest. It is short-lived and shown to a human, so
 * it is worth not answering an incorrect guess measurably faster.
 */

import type { WebDatabase } from './database';
import { createToken, digestsMatch, hashToken } from './tokens';
import { insertUser, prepareUser, type UserRecord } from './users';

export const BOOTSTRAP_TOKEN_LIFETIME_MS = 60 * 60 * 1000;

export class ProvisioningError extends Error {}

export interface BootstrapGrant {
	/** Shown once to the installing operator; only its digest is stored. */
	readonly token: string;
	readonly issuedAt: string;
	readonly expiresAt: string;
}

export type ClaimOutcome =
	| { readonly ok: true; readonly user: UserRecord }
	| {
			readonly ok: false;
			readonly reason: 'already_provisioned' | 'no_outstanding_token' | 'invalid_token' | 'expired';
	  };

interface BootstrapRow {
	token_hash: string;
	issued_at: string;
	expires_at: string;
}

export function isProvisioned(database: WebDatabase): boolean {
	const row = database
		.prepare<[], { initialized_at: string | null }>(
			'SELECT initialized_at FROM provisioning_state WHERE id = 1'
		)
		.get();
	return row?.initialized_at != null;
}

export function issueBootstrapToken(
	database: WebDatabase,
	now: Date,
	lifetimeMs: number = BOOTSTRAP_TOKEN_LIFETIME_MS
): BootstrapGrant {
	if (lifetimeMs <= 0) {
		throw new ProvisioningError('a bootstrap token must be given a positive lifetime');
	}
	if (isProvisioned(database)) {
		throw new ProvisioningError(
			'this appliance is already provisioned; create further accounts as an administrator'
		);
	}

	const token = createToken();
	const issuedAt = now.toISOString();
	const expiresAt = new Date(now.getTime() + lifetimeMs).toISOString();
	const issue = database.transaction(() => {
		// Supersede rather than accumulate: two live bootstrap credentials are
		// two chances for the wrong person to be first.
		database.prepare('DELETE FROM bootstrap_tokens WHERE claimed_at IS NULL').run();
		database
			.prepare(
				'INSERT INTO bootstrap_tokens (token_hash, issued_at, expires_at, claimed_at,' +
					' claimed_user_id) VALUES (?, ?, ?, NULL, NULL)'
			)
			.run(hashToken(token), issuedAt, expiresAt);
	});
	issue.immediate();

	return { token, issuedAt, expiresAt };
}

export interface ClaimOptions {
	readonly token: string;
	readonly username: string;
	readonly password: string;
}

/** Spend the bootstrap token to create the first administrator. */
export async function claimBootstrapToken(
	database: WebDatabase,
	options: ClaimOptions,
	now: Date
): Promise<ClaimOutcome> {
	if (isProvisioned(database)) {
		return { ok: false, reason: 'already_provisioned' };
	}

	const outstanding = database
		.prepare<[], BootstrapRow>(
			'SELECT token_hash, issued_at, expires_at FROM bootstrap_tokens WHERE claimed_at IS NULL'
		)
		.get();
	if (outstanding === undefined) {
		return { ok: false, reason: 'no_outstanding_token' };
	}
	if (!digestsMatch(outstanding.token_hash, hashToken(options.token))) {
		return { ok: false, reason: 'invalid_token' };
	}
	if (outstanding.expires_at <= now.toISOString()) {
		return { ok: false, reason: 'expired' };
	}

	// Validate and hash before opening the transaction, both because Argon2id
	// must not run under a write lock and because a rejected username or
	// password then leaves the token usable for the operator's next attempt.
	const prepared = await prepareUser({
		username: options.username,
		password: options.password,
		role: 'administrator'
	});

	// Creating the administrator, spending the token and closing the window are
	// one write. A token claimed concurrently rolls the account back rather than
	// leaving an administrator nobody asked for.
	const claim = database.transaction((): UserRecord => {
		// The account row exists first, because the claim record references it.
		const created = insertUser(database, prepared, now);
		const spent = database
			.prepare(
				'UPDATE bootstrap_tokens SET claimed_at = ?, claimed_user_id = ?' +
					' WHERE token_hash = ? AND claimed_at IS NULL'
			)
			.run(now.toISOString(), created.userId, outstanding.token_hash);
		if (spent.changes === 0) {
			throw new ProvisioningError('the bootstrap token was claimed concurrently');
		}
		database
			.prepare('UPDATE provisioning_state SET initialized_at = ? WHERE id = 1')
			.run(now.toISOString());
		return created;
	});

	return { ok: true, user: claim.immediate() };
}

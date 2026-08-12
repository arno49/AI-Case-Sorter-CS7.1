import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { WebDatabase } from './database';
import { NOW, PASSWORD, at, memoryDatabase } from './harness';
import {
	BOOTSTRAP_TOKEN_LIFETIME_MS,
	ProvisioningError,
	claimBootstrapToken,
	isProvisioned,
	issueBootstrapToken
} from './provisioning';
import { WeakPasswordError } from './passwords';
import { hashToken } from './tokens';
import { InvalidUsernameError, authenticate, listUsers, userCount } from './users';

let database: WebDatabase;

beforeEach(() => {
	database = memoryDatabase();
});

afterEach(() => {
	database.close();
});

const ADMIN = { username: 'installer', password: PASSWORD };

describe('a fresh appliance', () => {
	it('is unprovisioned and has no account to log in as', () => {
		expect(isProvisioned(database)).toBe(false);
		expect(userCount(database)).toBe(0);
	});

	it('cannot be provisioned without a token that was issued', async () => {
		const outcome = await claimBootstrapToken(database, { ...ADMIN, token: 'invented' }, NOW);

		expect(outcome).toEqual({ ok: false, reason: 'no_outstanding_token' });
		expect(userCount(database)).toBe(0);
	});
});

describe('issueBootstrapToken', () => {
	it('stores only the digest and bounds the token by an expiry', () => {
		const grant = issueBootstrapToken(database, NOW);

		expect(grant.expiresAt).toBe(at(BOOTSTRAP_TOKEN_LIFETIME_MS).toISOString());
		const row = database
			.prepare<[], { token_hash: string; claimed_at: string | null }>(
				'SELECT token_hash, claimed_at FROM bootstrap_tokens'
			)
			.get();
		expect(row?.token_hash).toBe(hashToken(grant.token));
		expect(row?.claimed_at).toBeNull();
	});

	it('supersedes an outstanding token, so only one is ever live', async () => {
		const first = issueBootstrapToken(database, NOW);

		const second = issueBootstrapToken(database, at(1000));

		expect(await claimBootstrapToken(database, { ...ADMIN, token: first.token }, at(1000))).toEqual(
			{ ok: false, reason: 'invalid_token' }
		);
		expect(
			await claimBootstrapToken(database, { ...ADMIN, token: second.token }, at(1000))
		).toMatchObject({ ok: true });
	});

	it('accepts a shorter lifetime and refuses a non-positive one', () => {
		const grant = issueBootstrapToken(database, NOW, 60_000);

		expect(grant.expiresAt).toBe(at(60_000).toISOString());
		expect(() => issueBootstrapToken(database, NOW, 0)).toThrow(ProvisioningError);
	});

	it('is refused once the appliance is provisioned', async () => {
		const grant = issueBootstrapToken(database, NOW);
		await claimBootstrapToken(database, { ...ADMIN, token: grant.token }, NOW);

		expect(() => issueBootstrapToken(database, at(1000))).toThrow('already provisioned');
	});
});

describe('claimBootstrapToken', () => {
	it('creates the first administrator and closes the window', async () => {
		const grant = issueBootstrapToken(database, NOW);

		const outcome = await claimBootstrapToken(database, { ...ADMIN, token: grant.token }, NOW);

		expect(outcome.ok).toBe(true);
		if (outcome.ok) {
			expect(outcome.user.role).toBe('administrator');
			expect(outcome.user.username).toBe('installer');
		}
		expect(isProvisioned(database)).toBe(true);
		expect(await authenticate(database, ADMIN)).toMatchObject({ ok: true });
	});

	it('is single use', async () => {
		const grant = issueBootstrapToken(database, NOW);
		await claimBootstrapToken(database, { ...ADMIN, token: grant.token }, NOW);

		const second = await claimBootstrapToken(
			database,
			{ token: grant.token, username: 'intruder', password: PASSWORD },
			at(1000)
		);

		expect(second).toEqual({ ok: false, reason: 'already_provisioned' });
		expect(listUsers(database).map((user) => user.username)).toEqual(['installer']);
	});

	it('refuses an expired token and leaves the appliance unprovisioned', async () => {
		const grant = issueBootstrapToken(database, NOW);

		const outcome = await claimBootstrapToken(
			database,
			{ ...ADMIN, token: grant.token },
			at(BOOTSTRAP_TOKEN_LIFETIME_MS)
		);

		expect(outcome).toEqual({ ok: false, reason: 'expired' });
		expect(isProvisioned(database)).toBe(false);
		expect(userCount(database)).toBe(0);
	});

	it('refuses a token that does not match the outstanding one', async () => {
		issueBootstrapToken(database, NOW);

		const outcome = await claimBootstrapToken(database, { ...ADMIN, token: 'guessed' }, NOW);

		expect(outcome).toEqual({ ok: false, reason: 'invalid_token' });
		expect(userCount(database)).toBe(0);
	});

	it('leaves the token usable when the requested account is rejected', async () => {
		const grant = issueBootstrapToken(database, NOW);

		await expect(
			claimBootstrapToken(database, { token: grant.token, username: 'x', password: PASSWORD }, NOW)
		).rejects.toThrow(InvalidUsernameError);
		await expect(
			claimBootstrapToken(
				database,
				{ token: grant.token, username: 'installer', password: 'short' },
				NOW
			)
		).rejects.toThrow(WeakPasswordError);

		expect(isProvisioned(database)).toBe(false);
		expect(
			await claimBootstrapToken(database, { ...ADMIN, token: grant.token }, NOW)
		).toMatchObject({ ok: true });
	});

	it('cannot be re-opened by clearing the provisioning row', async () => {
		const grant = issueBootstrapToken(database, NOW);
		await claimBootstrapToken(database, { ...ADMIN, token: grant.token }, NOW);

		expect(() =>
			database.prepare('UPDATE provisioning_state SET initialized_at = NULL WHERE id = 1').run()
		).toThrow('provisioning cannot be re-opened in place');
	});

	it('records the account the token was spent on', async () => {
		const grant = issueBootstrapToken(database, NOW);

		const outcome = await claimBootstrapToken(database, { ...ADMIN, token: grant.token }, at(500));

		expect(outcome.ok).toBe(true);
		const row = database
			.prepare<[], { claimed_at: string; claimed_user_id: string }>(
				'SELECT claimed_at, claimed_user_id FROM bootstrap_tokens'
			)
			.get();
		expect(row?.claimed_at).toBe(at(500).toISOString());
		expect(row?.claimed_user_id).toBe(outcome.ok ? outcome.user.userId : undefined);
	});

	it('refuses a storage-level second claim of the same row', async () => {
		const grant = issueBootstrapToken(database, NOW);
		await claimBootstrapToken(database, { ...ADMIN, token: grant.token }, NOW);

		expect(() =>
			database
				.prepare('UPDATE bootstrap_tokens SET claimed_at = ? WHERE token_hash = ?')
				.run(at(1000).toISOString(), hashToken(grant.token))
		).toThrow('a bootstrap token can be claimed only once');
	});
});

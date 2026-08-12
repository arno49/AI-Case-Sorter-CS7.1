/**
 * Authorization through the real hook, for real accounts.
 *
 * The matrix and the route table are asserted on their own elsewhere. What
 * these cover is the wiring: that the hook consults the table for every
 * request, that it refuses rather than resolves when a route was never
 * declared, and that a missing page stays a missing page instead of becoming a
 * permission problem.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { startSession } from '$lib/server/auth/boundary';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser, type Role } from '$lib/server/auth/users';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';

import { browser, request, throughHook as through, type CookieJar } from './harness';
import { handle } from '../hooks.server';
import { load as dashboardLoad } from './+page.server';
import type { RequestEvent, ServerLoadEvent } from '@sveltejs/kit';

let directory: string;

const throughHook = (event: RequestEvent) => through(event, handle);

/** A browser holding a live session for a freshly created account of `role`. */
async function signedIn(role: Role): Promise<CookieJar> {
	const { config, database } = webRuntime();
	const user = await createUser(database, { username: role, password: PASSWORD, role }, new Date());
	const cookies = browser();
	startSession(database, cookies, user.userId, config.profile, new Date());
	return cookies;
}

beforeEach(() => {
	directory = mkdtempSync(join(tmpdir(), 'cs71-rbac-'));
	process.env.CS71_WEB_PROFILE = 'development';
	process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
});

afterEach(() => {
	closeWebRuntime();
	rmSync(directory, { recursive: true, force: true });
	delete process.env.CS71_WEB_PROFILE;
	delete process.env.CS71_WEB_DATABASE_PATH;
});

describe('the hook authorizes as well as authenticates', () => {
	it('admits a viewer to the dashboard, which asks only to read', async () => {
		const result = await throughHook(request('/', await signedIn('viewer')));

		expect('allowed' in result).toBe(true);
	});

	it('refuses a route that was added without a policy', async () => {
		// Not a page on disk: this is what a new route looks like to the hook
		// before anyone declares who may reach it.
		const result = await throughHook(
			request('/machine/stop', await signedIn('administrator'), { routeId: '/machine/stop' })
		);

		expect(result).toEqual({ refused: 403 });
	});

	it('sends an unauthenticated request to that same route to the login page', async () => {
		// Refusing with 403 would tell a stranger the route exists.
		const result = await throughHook(
			request('/machine/stop', browser(), { routeId: '/machine/stop' })
		);

		expect(result).toEqual({ redirectedTo: '/login' });
	});

	it('leaves a path that matched no route to answer as missing', async () => {
		const result = await throughHook(
			request('/nothing-here', await signedIn('operator'), { routeId: null })
		);

		expect('allowed' in result).toBe(true);
	});

	it('lets a signed-out browser nowhere, whatever the route', async () => {
		expect(await throughHook(request('/', browser()))).toEqual({ redirectedTo: '/login' });
	});
});

describe('what the dashboard tells the browser', () => {
	it('reports a viewer only what a viewer may do', async () => {
		const event = request('/', await signedIn('viewer'));
		await throughHook(event);

		expect(await dashboardLoad(event as unknown as ServerLoadEvent)).toEqual({
			username: 'viewer',
			role: 'viewer',
			capabilities: ['machine.read', 'machine.stop'],
			csrfToken: expect.any(String)
		});
	});

	it('reports an administrator everything that is grantable', async () => {
		const event = request('/', await signedIn('administrator'));
		await throughHook(event);

		const data = (await dashboardLoad(event as unknown as ServerLoadEvent)) as {
			capabilities: readonly string[];
		};

		expect(data.capabilities).toEqual([
			'machine.read',
			'machine.stop',
			'machine.operate',
			'machine.recover',
			'config.write',
			'users.manage'
		]);
	});
});

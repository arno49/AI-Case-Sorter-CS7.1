/**
 * The manual controls as an operator meets them.
 *
 * Rendering is the real page via `svelte/server`, read the way a keyboard
 * reads it. The end-to-end specs go further: the real load renders the page,
 * and the fields its forms carry — nothing recalled from anywhere else — are
 * submitted back through the real hook and action to a stand-in daemon on a
 * real socket. That is the shape of the PI-UI-001 criterion: a manual control
 * submits an idempotency- and generation-protected intent, not a bare button
 * press.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import type { RequestEvent, ServerLoadEvent } from '@sveltejs/kit';

import { recentAudit } from '$lib/server/audit';
import { startSession } from '$lib/server/auth/boundary';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser } from '$lib/server/auth/users';
import {
	acceptedOperation,
	replying,
	startFakeDaemon,
	type FakeDaemon,
	type RecordedRequest
} from '$lib/server/daemon/harness';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';
import type { MachineSnapshot } from '$lib/machine';
import { COMPLETION_WORDS } from '$lib/machine-status';

import Page from './+page.svelte';
import { actions as dashboardActions, load as dashboardLoad } from './+page.server';
import { handle } from '../hooks.server';
import { browser, request, throughHook } from './harness';
import { fieldText, firstFocusable, focusOrder, focusOrderIsDocumentOrder } from './rendered';

const OPERATION_ID = '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c';

function snapshot(overrides: Partial<MachineSnapshot> = {}): MachineSnapshot {
	return {
		api_version: 'v1',
		generation: 41,
		connection_state: 'READY',
		fault_state: 'CLEAR',
		ready: true,
		readiness_reason: null,
		active_operation: null,
		firmware: {
			firmware_version: null,
			protocol_version: 2,
			capabilities: {
				v2_available: true,
				crc_active: false,
				slot_count: 8,
				home_available: true,
				sort_available: true,
				feed_available: false,
				feed_unavailable_reason: 'the v2 feed lifecycle gate is NOT_EXECUTED'
			}
		},
		machine: { feed_homed: true, sort_homed: true, sorter_slot: null },
		faults: [],
		observed_at: '2026-08-11T12:00:00.000Z',
		...overrides
	} as MachineSnapshot;
}

const KEYS = {
	connect: 'a-connect-key-0123456789',
	home: 'a-home-key-0123456789ab',
	sort: 'a-sort-key-0123456789ab',
	feed: 'a-feed-key-0123456789ab'
};

interface PageForm {
	readonly control?: string;
	readonly error?: string;
	readonly operationId?: string;
	readonly state?: string;
}

function rendered(
	view: MachineSnapshot | null,
	options: { form?: PageForm; capabilities?: readonly string[] } = {}
): string {
	const capabilities = options.capabilities ?? ['machine.read', 'machine.stop', 'machine.operate'];
	return render(Page as never, {
		props: {
			data: {
				username: 'sam',
				role: 'operator',
				capabilities,
				csrfToken: 'a-token-for-this-browser',
				snapshot: view,
				unavailable: view === null ? 'The machine service is not answering.' : null,
				commandKeys: capabilities.includes('machine.operate') ? KEYS : null
			},
			form: options.form ?? null
		} as never
	}).body;
}

describe('what an operator is offered', () => {
	it('offers connect, home, sort and feed forms, each carrying the protected intent', () => {
		const html = rendered(snapshot());

		const home = firstFocusable(html, /^home$/i);
		expect(home?.form).toMatchObject({ action: '?/home', method: 'POST' });
		expect(home?.form?.fields).toMatchObject({
			csrf_token: 'a-token-for-this-browser',
			generation: '41',
			idempotency_key: KEYS.home,
			target: 'feeder'
		});

		const sort = firstFocusable(html, /move the sorter/i);
		expect(sort?.form).toMatchObject({ action: '?/sort', method: 'POST' });
		expect(sort?.form?.fields).toMatchObject({ generation: '41', slot: '0' });

		const connect = focusOrder(html).find((control) => /connect/i.test(control.name));
		expect(connect?.form).toMatchObject({ action: '?/connect', method: 'POST' });
	});

	it('offers exactly the slots the firmware advertises', () => {
		const slots = fieldText(rendered(snapshot()), 'sort-slots').join(' ');

		expect(slots).toContain('7');
		expect(slots).not.toContain('8');
	});

	it('withholds the controls from a role without machine.operate', () => {
		const html = rendered(snapshot(), { capabilities: ['machine.read', 'machine.stop'] });

		expect(html).not.toContain('?/home');
		expect(html).not.toContain('Manual controls');
	});

	it('keeps the software stop first in the tab order with the controls present', () => {
		const html = rendered(snapshot());

		expect(firstFocusable(html, /stop the machine/i)).toMatchObject({ position: 0 });
		expect(focusOrderIsDocumentOrder(html)).toBe(true);
	});
});

describe('what a withheld control says', () => {
	it('keeps feed disabled and shows the daemon reason for the unqualified gate', () => {
		const html = rendered(snapshot());

		const feed = focusOrder(html).find((control) => /feed one case/i.test(control.name));
		expect(feed?.disabled).toBe(true);
		expect(fieldText(html, 'control-reason').join(' ')).toContain(
			'the v2 feed lifecycle gate is NOT_EXECUTED'
		);
	});

	it('offers no command form against a machine that has not been read', () => {
		const html = rendered(null);

		expect(html).not.toContain('?/home');
		expect(fieldText(html, 'controls-withheld').join(' ')).toContain('has not been read');
		expect(firstFocusable(html, /stop the machine/i)).toMatchObject({ position: 0 });
	});
});

describe('what the page says about an answer', () => {
	it('shows an acceptance beside its own control, never worded as completion', () => {
		const html = rendered(snapshot(), {
			form: { control: 'home', operationId: OPERATION_ID, state: 'QUEUED' }
		});

		const acceptance = fieldText(html, 'command-accepted').join(' ');
		expect(acceptance).toContain(OPERATION_ID);
		expect(acceptance).toContain('acceptance');
		expect(acceptance).not.toMatch(COMPLETION_WORDS);
		expect(fieldText(html, 'stop-accepted')).toEqual([]);
	});

	it('shows a refusal beside its own control and not beside the stop', () => {
		const html = rendered(snapshot(), {
			form: { control: 'sort', error: 'The machine is not ready for that yet.' }
		});

		expect(fieldText(html, 'command-error').join(' ')).toContain('not ready');
		expect(fieldText(html, 'stop-error')).toEqual([]);
	});
});

describe('a manual command, end to end', () => {
	// SOFTWARE_SIMULATOR_ONLY: the daemon is a stand-in on a real socket. This
	// says the page and the action agree, not that a machine moved.
	let directory: string;
	let daemon: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-manual-'));
		daemon = await startFakeDaemon();
		daemon.answerWith((call, response) => {
			if (call.method === 'GET' && call.path === '/v1/snapshot') {
				replying(200, snapshot())(call, response);
			} else {
				replying(202, acceptedOperation({ state: 'QUEUED' }))(call, response);
			}
		});
		process.env.CS71_WEB_PROFILE = 'development';
		process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
		process.env.CS71D_SOCKET_PATH = daemon.socketPath;
		process.env.CS71_WEB_SERVICE_TOKEN_PATH = daemon.serviceTokenPath;
		vi.spyOn(console, 'error').mockImplementation(() => {});
	});

	afterEach(async () => {
		vi.restoreAllMocks();
		closeWebRuntime();
		await daemon.close();
		rmSync(directory, { recursive: true, force: true });
		delete process.env.CS71_WEB_PROFILE;
		delete process.env.CS71_WEB_DATABASE_PATH;
		delete process.env.CS71D_SOCKET_PATH;
		delete process.env.CS71_WEB_SERVICE_TOKEN_PATH;
	});

	/** The home form as the served page carries it, for an operator session. */
	async function servedHomeForm(): Promise<{
		fields: Record<string, string>;
		cookies: ReturnType<typeof browser>;
	}> {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'operator', password: PASSWORD, role: 'operator' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/', cookies, { routeId: '/' });
		await throughHook(opened, handle);
		const data = await dashboardLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data, form: null } as never }).body;

		const home = firstFocusable(html, /^home$/i);
		expect(home?.form).toMatchObject({ action: '?/home', method: 'POST' });
		return { fields: { ...home!.form!.fields }, cookies };
	}

	async function submitted(
		fields: Record<string, string>,
		cookies: ReturnType<typeof browser>
	): Promise<unknown> {
		const pressed = request('/', cookies, { form: fields, routeId: '/' });
		await throughHook(pressed, handle);
		return dashboardActions.home(pressed as unknown as RequestEvent & { route: { id: '/' } });
	}

	it('homes the feeder with exactly what the served page gave', async () => {
		const { fields, cookies } = await servedHomeForm();
		const answer = await submitted(fields, cookies);

		expect(answer).toMatchObject({ control: 'home', operationId: OPERATION_ID });

		const sent = daemon.requests.filter((call) => call.path === '/v1/operations/home');
		expect(sent).toHaveLength(1);
		expect(sent[0].headers['if-match-generation']).toBe('41');
		expect(sent[0].headers['idempotency-key']).toBe(fields.idempotency_key);
		expect(JSON.parse(sent[0].body)).toMatchObject({ target: 'feeder' });

		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'machine.home',
			outcome: 'accepted',
			operationId: OPERATION_ID
		});
	});

	it('makes a resubmission the same command: the same idempotency key is sent', async () => {
		const { fields, cookies } = await servedHomeForm();
		await submitted(fields, cookies);
		await submitted(fields, cookies);

		const keys = daemon.requests
			.filter((call: RecordedRequest) => call.path === '/v1/operations/home')
			.map((call) => call.headers['idempotency-key']);
		expect(keys).toHaveLength(2);
		expect(keys[0]).toBe(keys[1]);
	});

	it('tells a stale page to reload rather than acting on what it saw', async () => {
		const { fields, cookies } = await servedHomeForm();
		daemon.answerWith(
			replying(409, {
				code: 'STALE_GENERATION',
				message: 'the machine has moved on',
				request_id: 'r-1'
			})
		);

		const answer = await submitted(fields, cookies);

		expect(answer).toMatchObject({
			status: 409,
			data: { control: 'home', error: expect.stringContaining('Reload') }
		});
	});
});

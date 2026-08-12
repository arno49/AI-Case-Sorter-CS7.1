/**
 * Recovery as an operator meets it.
 *
 * Rendering is the real page via `svelte/server`. The end-to-end spec goes
 * further: the real load renders the page, and submitting exactly the fields
 * the served form carries — plus the confirmation checkbox — reaches a
 * stand-in daemon on a real socket as a recovery command with the confirmation
 * the contract requires.
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
	type FakeDaemon
} from '$lib/server/daemon/harness';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';
import type { MachineSnapshot } from '$lib/machine';
import { COMPLETION_WORDS } from '$lib/machine-status';

import Page from './+page.svelte';
import { actions as dashboardActions, load as dashboardLoad } from './+page.server';
import { handle } from '../hooks.server';
import { browser, raisedBy, request, throughHook } from './harness';
import { fieldText, firstFocusable, focusOrder } from './rendered';

const OPERATION_ID = '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c';
const RECOVERY_KEY = 'a-recovery-key-0123456789ab';

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
	const capabilities = options.capabilities ?? [
		'machine.read',
		'machine.stop',
		'machine.operate',
		'machine.recover'
	];
	return render(Page as never, {
		props: {
			data: {
				username: 'ada',
				role: 'administrator',
				capabilities,
				csrfToken: 'a-token-for-this-browser',
				snapshot: view,
				unavailable: view === null ? 'The machine service is not answering.' : null,
				commandKeys: null,
				recoveryKey: capabilities.includes('machine.recover') ? RECOVERY_KEY : null
			},
			form: options.form ?? null
		} as never
	}).body;
}

describe('who is offered recovery', () => {
	it('offers the recovery form to an administrator, carrying the protected intent', () => {
		const html = rendered(snapshot());

		const button = focusOrder(html).find((control) => /attempt recovery/i.test(control.name));
		expect(button?.disabled).toBe(false);
		expect(button?.form).toMatchObject({ action: '?/recover', method: 'POST' });
		expect(button?.form?.fields).toMatchObject({
			csrf_token: 'a-token-for-this-browser',
			generation: '41',
			idempotency_key: RECOVERY_KEY
		});
	});

	it('withholds recovery from a role without machine.recover', () => {
		const html = rendered(snapshot(), {
			capabilities: ['machine.read', 'machine.stop', 'machine.operate']
		});

		expect(html).not.toContain('?/recover');
		expect(html).not.toContain('id="recovery"');
	});

	it('withholds recovery while a session is already being established', () => {
		const html = rendered(snapshot({ connection_state: 'CONNECTING' }));

		const button = focusOrder(html).find((control) => /attempt recovery/i.test(control.name));
		expect(button?.disabled).toBe(true);
	});

	it('keeps the software stop first in the tab order with recovery present', () => {
		expect(firstFocusable(rendered(snapshot()), /stop the machine/i)).toMatchObject({
			position: 0
		});
	});
});

describe('what the page says about a recovery answer', () => {
	it('shows an acceptance beside its own control, never worded as completion', () => {
		const html = rendered(snapshot(), {
			form: { control: 'recover', operationId: OPERATION_ID, state: 'QUEUED' }
		});

		const acceptance = fieldText(html, 'recovery-accepted').join(' ');
		expect(acceptance).toContain(OPERATION_ID);
		expect(acceptance).toContain('acceptance');
		expect(acceptance).not.toMatch(COMPLETION_WORDS);
		expect(fieldText(html, 'stop-accepted')).toEqual([]);
	});

	it('shows a refusal beside its own control and not beside the stop', () => {
		const html = rendered(snapshot(), {
			form: { control: 'recover', error: 'The machine changed since this page was loaded.' }
		});

		expect(fieldText(html, 'recovery-error').join(' ')).toContain('changed since');
		expect(fieldText(html, 'stop-error')).toEqual([]);
	});
});

describe('a recovery, end to end', () => {
	// SOFTWARE_SIMULATOR_ONLY: the daemon is a stand-in on a real socket. This
	// says the page and the action agree, not that a machine recovered.
	let directory: string;
	let daemon: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-recovery-'));
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

	async function servedRecoveryForm(): Promise<{
		fields: Record<string, string>;
		cookies: ReturnType<typeof browser>;
	}> {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'ada', password: PASSWORD, role: 'administrator' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/', cookies, { routeId: '/' });
		await throughHook(opened, handle);
		const data = await dashboardLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data, form: null } as never }).body;

		const button = focusOrder(html).find((control) => /attempt recovery/i.test(control.name));
		expect(button?.form).toMatchObject({ action: '?/recover', method: 'POST' });
		return { fields: { ...button!.form!.fields }, cookies };
	}

	it('reaches the daemon as a recovery command with the confirmation set', async () => {
		const { fields, cookies } = await servedRecoveryForm();

		const pressed = request('/', cookies, {
			form: { ...fields, confirm: 'true' },
			routeId: '/'
		});
		await throughHook(pressed, handle);
		const answer = await dashboardActions.recover(
			pressed as unknown as RequestEvent & { route: { id: '/' } }
		);

		expect(answer).toMatchObject({ control: 'recover', operationId: OPERATION_ID });
		const sent = daemon.requests.filter((call) => call.path === '/v1/session/recover');
		expect(sent).toHaveLength(1);
		expect(JSON.parse(sent[0].body)).toMatchObject({ confirm_uncertain_recovery: true });

		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'machine.recover',
			outcome: 'accepted',
			operationId: OPERATION_ID
		});
	});

	it('refuses a recovery submitted without the confirmation checkbox, sending nothing', async () => {
		const { fields, cookies } = await servedRecoveryForm();

		const pressed = request('/', cookies, { form: fields, routeId: '/' });
		await throughHook(pressed, handle);
		const answer = await dashboardActions.recover(
			pressed as unknown as RequestEvent & { route: { id: '/' } }
		);

		expect(answer).toMatchObject({ status: 400, data: { control: 'recover' } });
		expect(daemon.requests.filter((call) => call.path === '/v1/session/recover')).toHaveLength(0);

		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'machine.recover',
			outcome: 'refused'
		});
	});

	it('refuses recovery from an operator, who lacks machine.recover', async () => {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'sam', password: PASSWORD, role: 'operator' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const pressed = request('/', cookies, {
			form: { csrf_token: 'ignored', generation: '41', idempotency_key: 'x', confirm: 'true' },
			routeId: '/'
		});
		await throughHook(pressed, handle);

		const raised = await raisedBy(() =>
			dashboardActions.recover(pressed as unknown as RequestEvent & { route: { id: '/' } })
		);
		expect(raised).toMatchObject({ status: 403 });
	});
});

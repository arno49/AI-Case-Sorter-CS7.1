/**
 * The system view as an operator meets it.
 *
 * Rendering is the real page via `svelte/server`. The end-to-end spec goes
 * further: the real load reads both the snapshot and the system facts through
 * the real hook from a stand-in daemon on a real socket.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import type { ServerLoadEvent } from '@sveltejs/kit';

import { startSession } from '$lib/server/auth/boundary';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser } from '$lib/server/auth/users';
import { replying, startFakeDaemon, type FakeDaemon } from '$lib/server/daemon/harness';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';
import type { MachineSnapshot, System } from '$lib/machine';

import Page from './+page.svelte';
import { load as systemLoad } from './+page.server';
import { handle } from '../../hooks.server';
import { browser, request, throughHook } from '../harness';
import { fieldText, visibleText } from '../rendered';

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
			firmware_version: '7.1.260714.6',
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

function system(overrides: Partial<System> = {}): System {
	return {
		api_version: 'v1',
		dtr_gate_status: 'NOT_EXECUTED',
		observed_at: '2026-08-11T12:00:00.000Z',
		...overrides
	} as System;
}

interface PageData {
	readonly snapshot: MachineSnapshot | null;
	readonly system: System | null;
	readonly unavailable: string | null;
}

function rendered(data: PageData): string {
	return render(Page as never, { props: { data } as never }).body;
}

describe('what the system view shows', () => {
	it('shows the firmware version and protocol version the snapshot carries', () => {
		const html = rendered({ snapshot: snapshot(), system: system(), unavailable: null });

		expect(fieldText(html, 'system-firmware').join(' ')).toContain('7.1.260714.6');
		expect(fieldText(html, 'system-firmware').join(' ')).toContain('Protocol version 2');
	});

	it('says not reported when the controller gave no version', () => {
		const html = rendered({
			snapshot: snapshot({ firmware: { firmware_version: null, protocol_version: 1 } } as never),
			system: system(),
			unavailable: null
		});

		expect(fieldText(html, 'system-firmware').join(' ')).toContain('Not reported');
	});

	it('never presents NOT_EXECUTED as a pass', () => {
		const html = rendered({ snapshot: snapshot(), system: system(), unavailable: null });

		const gate = fieldText(html, 'system-dtr-gate').join(' ');
		expect(gate).toContain('NOT_EXECUTED');
		expect(gate).toContain('not a pass');
	});

	it('says storage health is not reported rather than inventing a value', () => {
		const html = rendered({ snapshot: snapshot(), system: system(), unavailable: null });

		expect(fieldText(html, 'system-storage').join(' ')).toBe('Not reported by this service.');
	});

	it('describes journal health as inferred from faults, not a dedicated check', () => {
		const html = rendered({ snapshot: snapshot(), system: system(), unavailable: null });

		expect(fieldText(html, 'system-journal').join(' ')).toContain('Inferred from recorded faults');
	});

	it('shows the servers words when the daemon is unavailable, and no facts', () => {
		const html = rendered({
			snapshot: null,
			system: null,
			unavailable: 'The machine service is not answering.'
		});

		expect(visibleText(html)).toContain('not answering');
		expect(fieldText(html, 'system-dtr-gate')).toEqual([]);
	});
});

describe('reading the system view, end to end', () => {
	// SOFTWARE_SIMULATOR_ONLY: the daemon is a stand-in on a real socket. This
	// says the page and the load agree, not that any gate was qualified.
	let directory: string;
	let daemon: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-system-'));
		daemon = await startFakeDaemon();
		daemon.answerWith((call, response) => {
			if (call.path === '/v1/snapshot') {
				replying(200, snapshot())(call, response);
			} else if (call.path === '/v1/system') {
				replying(200, system())(call, response);
			} else {
				replying(404, { code: 'RESOURCE_NOT_FOUND', message: 'no' })(call, response);
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

	it('reads both the snapshot and the system facts through the real hook', async () => {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/system', cookies, { routeId: '/system' });
		await throughHook(opened, handle);
		const data = await systemLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data } as never }).body;

		expect(fieldText(html, 'system-dtr-gate').join(' ')).toContain('NOT_EXECUTED');
		const paths = daemon.requests.map((call) => call.path);
		expect(paths).toEqual(expect.arrayContaining(['/v1/snapshot', '/v1/system']));
	});

	it('reports the daemon as unavailable rather than failing the page', async () => {
		daemon.answerWith((_call, response) => {
			response.destroy();
		});
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/system', cookies, { routeId: '/system' });
		await throughHook(opened, handle);
		const data = await systemLoad(opened as unknown as ServerLoadEvent);

		expect((data as PageData).snapshot).toBeNull();
		expect((data as PageData).system).toBeNull();
		expect((data as PageData).unavailable).toBeTruthy();
	});
});

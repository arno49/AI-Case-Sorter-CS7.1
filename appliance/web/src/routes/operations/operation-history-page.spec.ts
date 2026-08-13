/**
 * The operation history as an operator meets it.
 *
 * Rendering is the real page via `svelte/server`. The end-to-end spec goes
 * further: the real load reads a query string through the real hook, and the
 * request the daemon actually saw is what the spec checks — the same shape
 * PI-UI-001 asks for, a read this time rather than a command.
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
import { COMPLETION_WORDS } from '$lib/machine-status';
import type { Operation, OperationPage } from '$lib/machine';

import Page from './+page.svelte';
import { load as historyLoad } from './+page.server';
import { handle } from '../../hooks.server';
import { checkAccessibility } from '../accessibility';
import { browser, request, throughHook } from '../harness';
import { fieldText, focusOrder, visibleText } from '../rendered';

function operation(overrides: Partial<Operation> = {}): Operation {
	return {
		api_version: 'v1',
		operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
		type: 'SORT',
		state: 'SUCCEEDED',
		actor: { user_id: 'sam', role: 'operator' },
		created_at: '2026-08-11T12:00:00.000Z',
		deadline_at: '2026-08-11T12:00:05.000Z',
		generation: 41,
		trusted_terminal: true,
		terminal_at: '2026-08-11T12:00:02.000Z',
		outcome: 'COMPLETED',
		...overrides
	} as Operation;
}

function page(items: readonly Operation[], nextCursor: string | null = null): OperationPage {
	return { api_version: 'v1', items, next_cursor: nextCursor };
}

interface PageData {
	readonly page: OperationPage | null;
	readonly unavailable: string | null;
	readonly filter: { readonly state: string | null; readonly type: string | null };
}

function rendered(data: PageData): string {
	return render(Page as never, { props: { data } as never }).body;
}

describe('what the history page shows', () => {
	it('lists each operation with its actor, creation time and daemon identifier', () => {
		const html = rendered({
			page: page([operation()]),
			unavailable: null,
			filter: { state: null, type: null }
		});

		expect(fieldText(html, 'history-row').join(' ')).toContain('Sort');
		expect(fieldText(html, 'history-operation-id').join(' ')).toContain(
			'0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c'
		);
		expect(fieldText(html, 'history-actor').join(' ')).toContain('sam');
		expect(fieldText(html, 'history-created-at').join(' ')).toContain('2026-08-11T12:00:00.000Z');
	});

	it('never words an accepted or running row as a completion', () => {
		const html = rendered({
			page: page([
				operation({
					state: 'ACCEPTED',
					trusted_terminal: false,
					terminal_at: undefined,
					outcome: undefined
				})
			]),
			unavailable: null,
			filter: { state: null, type: null }
		});

		expect(fieldText(html, 'history-summary').join(' ')).not.toMatch(COMPLETION_WORDS);
	});

	it('describes an unconfirmed terminal as not known, not by its state name alone', () => {
		const html = rendered({
			page: page([
				operation({ state: 'UNCERTAIN', trusted_terminal: false, outcome: 'UNCERTAIN' })
			]),
			unavailable: null,
			filter: { state: null, type: null }
		});

		expect(fieldText(html, 'history-summary').join(' ')).toContain('not known');
	});

	it('tells an empty unfiltered page apart from an empty filtered one', () => {
		expect(
			fieldText(
				rendered({ page: page([]), unavailable: null, filter: { state: null, type: null } }),
				'history-empty'
			).join(' ')
		).toBe('No operations recorded yet.');

		expect(
			fieldText(
				rendered({
					page: page([]),
					unavailable: null,
					filter: { state: 'SUCCEEDED', type: null }
				}),
				'history-empty'
			).join(' ')
		).toBe('No operations match this filter.');
	});

	it('shows the servers words when the daemon is unavailable, and no rows', () => {
		const html = rendered({
			page: null,
			unavailable: 'The machine service is not answering.',
			filter: { state: null, type: null }
		});

		expect(visibleText(html)).toContain('not answering');
		expect(fieldText(html, 'history-row')).toEqual([]);
	});

	it('offers a next-page link only when the daemon gave a cursor, carrying the filter forward', () => {
		const html = rendered({
			page: page([operation()], 'opaque-cursor-1'),
			unavailable: null,
			filter: { state: 'SUCCEEDED', type: null }
		});

		const next = focusOrder(html).find((control) => /next page/i.test(control.name));
		expect(next).toBeDefined();
		const href = html.match(/data-field="history-next"\s+href="([^"]*)"/)?.[1] ?? '';
		expect(href).toContain('state=SUCCEEDED');
		expect(href).toContain('cursor=opaque-cursor-1');
	});

	it('marks the selected filter option so a reload keeps what was chosen', () => {
		const html = rendered({
			page: page([]),
			unavailable: null,
			filter: { state: 'FAILED', type: 'HOME' }
		});

		expect(html).toMatch(/<option value="FAILED" selected[^>]*>Failed<\/option>/);
		expect(html).toMatch(/<option value="HOME" selected[^>]*>Home<\/option>/);
	});
});

describe('reading the history, end to end', () => {
	// SOFTWARE_SIMULATOR_ONLY: the daemon is a stand-in on a real socket. This
	// says the page and the load agree on a query string, not that a machine
	// produced this history.
	let directory: string;
	let daemon: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-history-'));
		daemon = await startFakeDaemon();
		daemon.answerWith(replying(200, page([operation()], 'opaque-cursor-1')));
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

	it('forwards a recognised filter to the daemon and renders what it answered', async () => {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/operations?state=SUCCEEDED&type=SORT', cookies, {
			routeId: '/operations'
		});
		await throughHook(opened, handle);
		const data = await historyLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data } as never }).body;

		expect(fieldText(html, 'history-operation-id').join(' ')).toContain(
			'0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c'
		);
		const sent = daemon.requests.filter((call) => call.path.startsWith('/v1/operations?'));
		expect(sent).toHaveLength(1);
		expect(sent[0].path).toContain('state=SUCCEEDED');
		expect(sent[0].path).toContain('type=SORT');
	});

	it('drops a filter value the contract does not define rather than sending it on', async () => {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/operations?state=NOT_A_REAL_STATE', cookies, {
			routeId: '/operations'
		});
		await throughHook(opened, handle);
		await historyLoad(opened as unknown as ServerLoadEvent);

		const sent = daemon.lastRequest();
		expect(sent.path).not.toContain('state=');
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

		const opened = request('/operations', cookies, { routeId: '/operations' });
		await throughHook(opened, handle);
		const data = await historyLoad(opened as unknown as ServerLoadEvent);

		expect((data as PageData).page).toBeNull();
		expect((data as PageData).unavailable).toBeTruthy();
	});
});

describe('automated accessibility checks (PI-SWQ-002)', () => {
	it('meets WCAG 2.1/2.2 A/AA rules axe-core can evaluate without CSS layout', async () => {
		const html = rendered({
			page: page([operation()]),
			unavailable: null,
			filter: { state: null, type: null }
		});

		const report = await checkAccessibility(html);

		expect(report.violations).toEqual([]);
	});
});

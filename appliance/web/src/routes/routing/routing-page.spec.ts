/**
 * The routing profile screen as an operator meets it.
 *
 * Rendering is the real page via `svelte/server`. The end-to-end spec goes
 * further: the real load and the real start/stop actions read and write
 * through the real hook, against a stand-in `cs71-vision` on a real socket.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';
import type { RequestEvent, ServerLoadEvent } from '@sveltejs/kit';

import { CSRF_FIELD } from '$lib/server/auth/csrf';
import { startSession } from '$lib/server/auth/boundary';
import { PASSWORD } from '$lib/server/auth/harness';
import { createUser } from '$lib/server/auth/users';
import { recentAudit } from '$lib/server/audit';
import { replying, startFakeDaemon, type FakeDaemon } from '$lib/server/daemon/harness';
import { closeWebRuntime, webRuntime } from '$lib/server/runtime';
import type { RoutingState } from '$lib/routing';

import Page from './+page.svelte';
import { actions as routingActions, load as routingLoad } from './+page.server';
import { handle } from '../../hooks.server';
import { checkAccessibility } from '../accessibility';
import { browser, csrfFor, request, throughHook, type CookieJar } from '../harness';
import { fieldText, focusOrderIsDocumentOrder, visibleText } from '../rendered';

function routing(overrides: Partial<RoutingState> = {}): RoutingState {
	return {
		active: false,
		kind: null,
		startedAt: null,
		sourceGroup: null,
		legend: [],
		...overrides
	};
}

interface PageData {
	readonly routing: RoutingState | null;
	readonly unavailable: string | null;
	readonly canOperate: boolean;
	readonly csrfToken: string;
}

function rendered(data: PageData, form: unknown = null): string {
	return render(Page as never, { props: { data, form } as never }).body;
}

function pageData(overrides: Partial<PageData> = {}): PageData {
	return {
		routing: routing(),
		unavailable: null,
		canOperate: true,
		csrfToken: 'csrf',
		...overrides
	};
}

describe('what the routing view shows', () => {
	it('says no run is active to an account that cannot start one', () => {
		const html = rendered(pageData({ canOperate: false }));

		expect(fieldText(html, 'routing-inactive')).not.toEqual([]);
	});

	it('offers the three start forms to an account that can operate the machine', () => {
		const html = rendered(pageData());

		expect(html).toContain('Start fixed-map run');
		expect(html).toContain('Start dynamic run');
		expect(html).toContain('Start two-pass run');
	});

	it('shows the active profile and started time throughout the run', () => {
		const html = rendered(
			pageData({
				routing: routing({
					active: true,
					kind: 'fixed',
					startedAt: '2026-08-12T12:00:00.000Z'
				})
			})
		);

		expect(fieldText(html, 'routing-kind').join(' ')).toContain('fixed');
		expect(fieldText(html, 'routing-started-at').join(' ')).toContain('2026-08-12T12:00:00.000Z');
	});

	it('shows the source group being refined for a two-pass run', () => {
		const html = rendered(
			pageData({
				routing: routing({ active: true, kind: 'two_pass', sourceGroup: 9 })
			})
		);

		expect(fieldText(html, 'routing-kind').join(' ')).toContain('9');
	});

	it('lists the legend, distinguishing overflow from a claimed class', () => {
		const html = rendered(
			pageData({
				routing: routing({
					active: true,
					kind: 'fixed',
					legend: [
						{ slot: 3, classId: 12, overflow: false },
						{ slot: 7, classId: null, overflow: true }
					]
				})
			})
		);

		expect(fieldText(html, 'routing-legend-3').join(' ')).toContain('class 12');
		expect(fieldText(html, 'routing-legend-7').join(' ')).toContain('everything else');
	});

	it('says no chute has been claimed yet for an active run with an empty legend', () => {
		const html = rendered(pageData({ routing: routing({ active: true, kind: 'dynamic' }) }));

		expect(fieldText(html, 'routing-legend-empty')).not.toEqual([]);
	});

	it('offers a stop control only to an account that can operate the machine', () => {
		const active = routing({ active: true, kind: 'fixed' });

		const withCapability = rendered(pageData({ routing: active, canOperate: true }));
		const without = rendered(pageData({ routing: active, canOperate: false }));

		expect(withCapability).toContain('Stop this run');
		expect(without).not.toContain('Stop this run');
	});

	it('shows the servers words when cs71-vision is unavailable', () => {
		const html = rendered(
			pageData({ routing: null, unavailable: 'The classifier service is not answering.' })
		);

		expect(visibleText(html)).toContain('not answering');
	});
});

describe('reading and acting on the routing view, end to end', () => {
	let directory: string;
	let vision: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-routing-'));
		vision = await startFakeDaemon();
		vision.answerWith((call, response) => {
			if (call.path === '/v1/routing') {
				replying(200, {
					api_version: 'v1',
					active: false,
					kind: null,
					started_at: null,
					source_group: null,
					legend: []
				})(call, response);
			} else if (call.path === '/v1/routing/start') {
				replying(200, {
					api_version: 'v1',
					active: true,
					kind: 'fixed',
					started_at: '2026-08-12T12:00:00.000Z',
					source_group: null,
					legend: [
						{ slot: 3, class_id: 12, overflow: false },
						{ slot: 7, class_id: null, overflow: true }
					]
				})(call, response);
			} else if (call.path === '/v1/routing/stop') {
				replying(200, {
					api_version: 'v1',
					active: false,
					kind: null,
					started_at: null,
					source_group: null,
					legend: []
				})(call, response);
			} else {
				replying(404, { code: 'RESOURCE_NOT_FOUND', message: 'no' })(call, response);
			}
		});
		process.env.CS71_WEB_PROFILE = 'development';
		process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
		process.env.CS71D_SOCKET_PATH = join(directory, 'unused-daemon.sock');
		process.env.CS71_VISION_SOCKET_PATH = vision.socketPath;
		process.env.CS71_WEB_SERVICE_TOKEN_PATH = vision.serviceTokenPath;
		vi.spyOn(console, 'error').mockImplementation(() => {});
	});

	afterEach(async () => {
		vi.restoreAllMocks();
		closeWebRuntime();
		await vision.close();
		rmSync(directory, { recursive: true, force: true });
		delete process.env.CS71_WEB_PROFILE;
		delete process.env.CS71_WEB_DATABASE_PATH;
		delete process.env.CS71D_SOCKET_PATH;
		delete process.env.CS71_VISION_SOCKET_PATH;
		delete process.env.CS71_WEB_SERVICE_TOKEN_PATH;
	});

	async function signedIn(role: 'viewer' | 'operator' | 'administrator'): Promise<CookieJar> {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: role, password: PASSWORD, role },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());
		return cookies;
	}

	it('reads the inactive routing state through the real hook', async () => {
		const cookies = await signedIn('viewer');

		const opened = request('/routing', cookies, { routeId: '/routing' });
		await throughHook(opened, handle);
		const data = await routingLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data } as never }).body;

		expect(fieldText(html, 'routing-inactive')).not.toEqual([]);
		expect(vision.requests.some((call) => call.path === '/v1/routing')).toBe(true);
	});

	it('lets an operator start a fixed-map run, and audits it', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/routing', cookies, {
			form: {
				[CSRF_FIELD]: csrfFor(cookies),
				class_to_slot: '12:3\n45:5',
				overflow_slot: '7'
			},
			routeId: '/routing'
		});
		await throughHook(pressed, handle);

		const answer = await routingActions.startFixed(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'start', kind: 'fixed' });
		expect(vision.requests.some((call) => call.path === '/v1/routing/start')).toBe(true);
		const started = vision.requests.find((call) => call.path === '/v1/routing/start');
		expect(JSON.parse(started?.body ?? '{}')).toMatchObject({
			kind: 'fixed',
			class_to_slot: { 12: 3, 45: 5 },
			overflow_slot: 7
		});
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.routing',
			outcome: 'accepted'
		});
	});

	it('refuses a malformed class:slot pair, sending nothing', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/routing', cookies, {
			form: {
				[CSRF_FIELD]: csrfFor(cookies),
				class_to_slot: 'not-a-pair',
				overflow_slot: '7'
			},
			routeId: '/routing'
		});
		await throughHook(pressed, handle);

		const answer = await routingActions.startFixed(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ status: 400, data: { control: 'start' } });
		expect(vision.requests.some((call) => call.path === '/v1/routing/start')).toBe(false);
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.routing',
			outcome: 'refused'
		});
	});

	it('lets an operator start a dynamic run', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/routing', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies), available_slots: '1\n2\n3' },
			routeId: '/routing'
		});
		await throughHook(pressed, handle);

		const answer = await routingActions.startDynamic(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'start' });
		const started = vision.requests.find((call) => call.path === '/v1/routing/start');
		expect(JSON.parse(started?.body ?? '{}')).toMatchObject({
			kind: 'dynamic',
			available_slots: [1, 2, 3]
		});
	});

	it('lets an operator start a two-pass run naming its source group', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/routing', cookies, {
			form: {
				[CSRF_FIELD]: csrfFor(cookies),
				class_to_slot: '12:3',
				overflow_slot: '7',
				source_group: '9'
			},
			routeId: '/routing'
		});
		await throughHook(pressed, handle);

		const answer = await routingActions.startTwoPass(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'start' });
		const started = vision.requests.find((call) => call.path === '/v1/routing/start');
		expect(JSON.parse(started?.body ?? '{}')).toMatchObject({
			kind: 'two_pass',
			source_group: 9
		});
	});

	it('omits source_group from a two-pass run when left blank', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/routing', cookies, {
			form: {
				[CSRF_FIELD]: csrfFor(cookies),
				class_to_slot: '12:3',
				overflow_slot: '7',
				source_group: ''
			},
			routeId: '/routing'
		});
		await throughHook(pressed, handle);

		await routingActions.startTwoPass(pressed as unknown as RequestEvent);

		const started = vision.requests.find((call) => call.path === '/v1/routing/start');
		expect(JSON.parse(started?.body ?? '{}')).not.toHaveProperty('source_group');
	});

	it('lets an operator stop the active run, and audits it', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/routing', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			routeId: '/routing'
		});
		await throughHook(pressed, handle);

		const answer = await routingActions.stop(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'stop' });
		expect(vision.requests.some((call) => call.path === '/v1/routing/stop')).toBe(true);
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.routing',
			outcome: 'accepted'
		});
	});

	it('refuses a viewer permission to start a run, before anything reaches cs71-vision', async () => {
		const cookies = await signedIn('viewer');
		const pressed = request('/routing', cookies, {
			form: {
				[CSRF_FIELD]: csrfFor(cookies),
				class_to_slot: '12:3',
				overflow_slot: '7'
			},
			routeId: '/routing'
		});
		await throughHook(pressed, handle);

		await expect(
			routingActions.startFixed(pressed as unknown as RequestEvent)
		).rejects.toMatchObject({ status: 403 });
		expect(vision.requests.some((call) => call.path === '/v1/routing/start')).toBe(false);
	});
});

describe('automated accessibility checks (PI-SWQ-002)', () => {
	it('meets WCAG 2.1/2.2 A/AA rules with no run active (the start forms)', async () => {
		const report = await checkAccessibility(rendered(pageData()));

		expect(report.violations).toEqual([]);
	});

	it('meets WCAG 2.1/2.2 A/AA rules with a run active (the legend table)', async () => {
		const report = await checkAccessibility(
			rendered(
				pageData({
					routing: routing({
						active: true,
						kind: 'fixed',
						startedAt: '2026-08-11T12:00:00.000Z',
						legend: [{ slot: 3, classId: 12, overflow: false }]
					})
				})
			)
		);

		expect(report.violations).toEqual([]);
	});

	it('declares no tabindex that could pull a control ahead of document order', () => {
		expect(focusOrderIsDocumentOrder(rendered(pageData()))).toBe(true);
	});
});

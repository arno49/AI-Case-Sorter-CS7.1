/**
 * The dashboard as an operator meets it.
 *
 * These render the real page with `svelte/server` — the same rendering the
 * server does for the first response — and read the result the way a keyboard
 * and a screen reader would. What they hold still is the part of PI-UI-001 that
 * is about the page rather than the daemon: what the screen shows, what it may
 * call things, and that the software stop is reachable without a pointer.
 */

import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { render } from 'svelte/server';

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
import type { MachineSnapshot, Operation } from '$lib/machine';
import { COMPLETION_WORDS } from '$lib/machine-status';

import Page from './+page.svelte';
import { actions as dashboardActions, load as dashboardLoad } from './+page.server';
import { handle } from '../hooks.server';
import { checkAccessibility } from './accessibility';
import { browser, request, throughHook } from './harness';
import { fieldText, firstFocusable, focusOrderIsDocumentOrder, visibleText } from './rendered';
import type { RequestEvent, ServerLoadEvent } from '@sveltejs/kit';

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
		machine: { feed_homed: true, sort_homed: false, sorter_slot: null },
		faults: [],
		observed_at: '2026-08-11T12:00:00.000Z',
		...overrides
	} as MachineSnapshot;
}

function operation(overrides: Partial<Operation> = {}): Operation {
	return {
		api_version: 'v1',
		operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
		type: 'STOP',
		state: 'RUNNING',
		actor: { user_id: 'u', role: 'operator' },
		created_at: '2026-08-11T12:00:00.000Z',
		deadline_at: '2026-08-11T12:00:05.000Z',
		generation: 41,
		trusted_terminal: false,
		...overrides
	} as Operation;
}

interface PageForm {
	readonly control?: string;
	readonly error?: string;
	readonly operationId?: string;
	readonly state?: string;
	readonly generation?: number;
}

interface PageSuggestion {
	readonly slot: number;
	readonly confidence: number;
	readonly modelVersion: number;
	readonly suggestedAt: string;
	readonly primerPresent: boolean | null;
	readonly requiresConfirmation: boolean;
}

interface PageRouting {
	readonly active: boolean;
	readonly kind: 'fixed' | 'dynamic' | 'two_pass' | null;
	readonly startedAt: string | null;
	readonly sourceGroup: number | null;
	readonly legend: readonly unknown[];
}

function rendered(
	view: MachineSnapshot | null,
	options: {
		form?: PageForm;
		suggestion?: PageSuggestion | null;
		routing?: PageRouting | null;
	} = {}
): string {
	return render(Page as never, {
		props: {
			data: {
				username: 'sam',
				role: 'operator',
				capabilities: ['machine.read', 'machine.stop', 'machine.operate'],
				csrfToken: 'a-token-for-this-browser',
				snapshot: view,
				unavailable: view === null ? 'The machine service is not answering.' : null,
				suggestion: options.suggestion ?? null,
				routing: options.routing ?? null
			},
			form: options.form ?? null
		} as never
	}).body;
}

describe('reaching the stop with a keyboard', () => {
	it('puts the software stop first in the tab order, submitting the stop action', () => {
		const html = rendered(snapshot());

		const stop = firstFocusable(html, /stop the machine/i);
		expect(stop).toMatchObject({ position: 0, disabled: false });
		expect(stop?.form).toMatchObject({ action: '?/stop', method: 'POST' });
		expect(stop?.form?.fields).toHaveProperty('csrf_token');
	});

	it('declares no tabindex that could pull another control in front of it', () => {
		expect(focusOrderIsDocumentOrder(rendered(snapshot()))).toBe(true);
	});

	it('keeps the stop reachable when the machine cannot be read', () => {
		// The stop matters most on a bad day. A daemon that is not answering must
		// not take the control with it.
		const html = rendered(null);

		expect(firstFocusable(html, /stop the machine/i)).toMatchObject({ position: 0 });
		expect(visibleText(html)).toContain('The machine service is not answering.');
	});

	it('says beside the button that this is not an emergency stop', () => {
		expect(visibleText(rendered(snapshot()))).toContain('not an emergency stop');
	});
});

describe('what the dashboard shows', () => {
	it('shows connection, homing, faults and the snapshot generation from the snapshot', () => {
		const html = rendered(snapshot());

		expect(fieldText(html, 'connection').join(' ')).toContain('Connected');
		expect(fieldText(html, 'homing')).toEqual(['Homed', 'Not homed']);
		expect(fieldText(html, 'fault-state').join(' ')).toContain('Clear');
		expect(fieldText(html, 'generation')).toEqual(['41']);
	});

	it('shows the active operation with the daemon identifiers unrenamed', () => {
		const html = rendered(snapshot({ active_operation: operation() }));

		const active = fieldText(html, 'active-operation').join(' ');
		expect(active).toContain('RUNNING');
		expect(active).toContain('0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c');
	});

	it('shows each recorded fault in the daemon words the error boundary allowed', () => {
		const html = rendered(
			snapshot({
				fault_state: 'LATCHED',
				faults: [
					{
						fault_id: '9d6f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
						state: 'LATCHED',
						code: 'JOURNAL_UNAVAILABLE',
						source: 'journal',
						message: 'the journal is unavailable',
						opened_at: '2026-08-11T12:00:00.000Z'
					}
				]
			})
		);

		expect(fieldText(html, 'fault').join(' ')).toContain('JOURNAL_UNAVAILABLE');
	});

	it('presents an unhomed axis as not known when nothing was observed', () => {
		const html = rendered(
			snapshot({
				connection_state: 'DISCONNECTED',
				ready: false,
				readiness_reason: 'no session',
				machine: { feed_homed: false, sort_homed: false, sorter_slot: null }
			})
		);

		expect(fieldText(html, 'homing')).toEqual(['Not known', 'Not known']);
	});

	it('shows a suggested slot and confidence before the sort form, when cs71-vision has one', () => {
		const html = rendered(snapshot(), {
			suggestion: {
				slot: 3,
				confidence: 0.87,
				modelVersion: 2,
				suggestedAt: '2026-08-11T12:00:00.000Z',
				primerPresent: false,
				requiresConfirmation: false
			}
		});

		const shown = fieldText(html, 'vision-suggestion').join(' ');
		expect(shown).toContain('3');
		expect(shown).toContain('87%');
	});

	it('shows nothing about a suggestion when cs71-vision has none, not an empty field', () => {
		const html = rendered(snapshot(), { suggestion: null });

		expect(fieldText(html, 'vision-suggestion')).toEqual([]);
	});

	it('shows a primer confirmation notice when the primer axis is unknown', () => {
		// PI-VISION-010: today primer_present is always null/unknown from
		// cs71-vision, so requiresConfirmation is always true.
		const html = rendered(snapshot(), {
			suggestion: {
				slot: 3,
				confidence: 0.87,
				modelVersion: 2,
				suggestedAt: '2026-08-11T12:00:00.000Z',
				primerPresent: null,
				requiresConfirmation: true
			}
		});

		expect(fieldText(html, 'vision-primer-notice').join(' ')).toContain('unknown');
	});

	it('shows a primer confirmation notice as flagged, never as clear, when primer is present', () => {
		const html = rendered(snapshot(), {
			suggestion: {
				slot: 3,
				confidence: 0.87,
				modelVersion: 2,
				suggestedAt: '2026-08-11T12:00:00.000Z',
				primerPresent: true,
				requiresConfirmation: true
			}
		});

		expect(fieldText(html, 'vision-primer-notice').join(' ')).toContain('flagged');
	});

	it('shows no primer notice when the axis is confidently clear', () => {
		const html = rendered(snapshot(), {
			suggestion: {
				slot: 3,
				confidence: 0.87,
				modelVersion: 2,
				suggestedAt: '2026-08-11T12:00:00.000Z',
				primerPresent: false,
				requiresConfirmation: false
			}
		});

		expect(fieldText(html, 'vision-primer-notice')).toEqual([]);
	});

	it('shows the active routing profile throughout the run', () => {
		const html = rendered(snapshot(), {
			routing: {
				active: true,
				kind: 'fixed',
				startedAt: '2026-08-11T12:00:00.000Z',
				sourceGroup: null,
				legend: []
			}
		});

		expect(fieldText(html, 'routing-active-notice').join(' ')).toContain('fixed');
	});

	it('shows no routing notice when no run is active', () => {
		const html = rendered(snapshot(), {
			routing: { active: false, kind: null, startedAt: null, sourceGroup: null, legend: [] }
		});

		expect(fieldText(html, 'routing-active-notice')).toEqual([]);
	});
});

describe('what the dashboard refuses to say', () => {
	it('never labels an accepted stop as a completion', () => {
		const html = rendered(snapshot({ active_operation: operation({ state: 'ACCEPTED' }) }), {
			form: {
				control: 'stop',
				operationId: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
				state: 'ACCEPTED'
			}
		});

		const acceptance = fieldText(html, 'stop-accepted').join(' ');
		expect(acceptance).toContain('acceptance');
		expect(acceptance).not.toMatch(COMPLETION_WORDS);
		const active = fieldText(html, 'active-operation-summary').join(' ');
		expect(active).not.toMatch(COMPLETION_WORDS);
	});

	it('describes an unconfirmed terminal as not known, not as its state name alone', () => {
		const html = rendered(
			snapshot({
				active_operation: operation({
					state: 'UNCERTAIN',
					trusted_terminal: false,
					terminal_at: '2026-08-11T12:00:05.000Z',
					outcome: 'UNCERTAIN'
				})
			})
		);

		expect(fieldText(html, 'active-operation-summary').join(' ')).toContain('not known');
	});

	it('shows the servers words when the machine is unavailable, and no snapshot fields', () => {
		const html = rendered(null);

		expect(fieldText(html, 'generation')).toEqual([]);
		expect(visibleText(html)).toContain('not answering');
	});
});

describe('a keyboard-only flow, end to end', () => {
	// SOFTWARE_SIMULATOR_ONLY: the daemon is a stand-in on a real socket. This
	// says the page and the action agree, not that a machine stopped.
	let directory: string;
	let daemon: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-kbd-'));
		daemon = await startFakeDaemon();
		daemon.answerWith(replying(202, acceptedOperation()));
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

	it('finds the stop on the served page and activates it with what the page gave', async () => {
		// The whole path a keyboard takes: the real load renders the page, the
		// first control in the tab order is the stop, and submitting exactly the
		// fields that form carries — nothing recalled from anywhere else — reaches
		// the daemon as a stop command.
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/', cookies, { routeId: '/' });
		await throughHook(opened, handle);
		const data = await dashboardLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data, form: null } as never }).body;

		const stop = firstFocusable(html, /stop the machine/i);
		expect(stop).toMatchObject({ position: 0, disabled: false });
		expect(stop?.form).toMatchObject({ action: '?/stop', method: 'POST' });

		const pressed = request('/', cookies, { form: { ...stop!.form!.fields }, routeId: '/' });
		await throughHook(pressed, handle);
		const answer = await dashboardActions.stop(
			pressed as unknown as RequestEvent & { route: { id: '/' } }
		);

		expect(answer).toMatchObject({ operationId: expect.any(String) });
		const sent = daemon.requests.filter((call) => call.path === '/v1/machine/stop');
		expect(sent).toHaveLength(1);
	});
});

describe('the suggested slot, end to end', () => {
	// SOFTWARE_SIMULATOR_ONLY: both cs71d and cs71-vision are stand-ins on real
	// sockets. This says the page and the real hook agree, not that a real
	// model classified a real frame.
	let directory: string;
	let daemon: FakeDaemon;
	let vision: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-suggestion-'));
		daemon = await startFakeDaemon();
		daemon.answerWith(replying(200, snapshot()));
		vision = await startFakeDaemon();
		process.env.CS71_WEB_PROFILE = 'development';
		process.env.CS71_WEB_DATABASE_PATH = join(directory, 'web.db');
		process.env.CS71D_SOCKET_PATH = daemon.socketPath;
		process.env.CS71_VISION_SOCKET_PATH = vision.socketPath;
		process.env.CS71_WEB_SERVICE_TOKEN_PATH = daemon.serviceTokenPath;
		vi.spyOn(console, 'error').mockImplementation(() => {});
	});

	afterEach(async () => {
		vi.restoreAllMocks();
		closeWebRuntime();
		await daemon.close();
		await vision.close();
		rmSync(directory, { recursive: true, force: true });
		delete process.env.CS71_WEB_PROFILE;
		delete process.env.CS71_WEB_DATABASE_PATH;
		delete process.env.CS71D_SOCKET_PATH;
		delete process.env.CS71_VISION_SOCKET_PATH;
		delete process.env.CS71_WEB_SERVICE_TOKEN_PATH;
	});

	async function loadedPage(): Promise<string> {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/', cookies, { routeId: '/' });
		await throughHook(opened, handle);
		const data = await dashboardLoad(opened as unknown as ServerLoadEvent);
		return render(Page as never, { props: { data, form: null } as never }).body;
	}

	it('reads the current suggestion through the real hook', async () => {
		vision.answerWith((call, response) => {
			if (call.path === '/v1/suggestion') {
				replying(200, {
					api_version: 'v1',
					suggestion: {
						slot: 3,
						confidence: 0.87,
						model_version: 2,
						suggested_at: '2026-08-11T12:00:00.000Z',
						primer_present: null,
						requires_confirmation: true
					}
				})(call, response);
			} else {
				replying(404, { code: 'RESOURCE_NOT_FOUND', message: 'no' })(call, response);
			}
		});

		const html = await loadedPage();

		const shown = fieldText(html, 'vision-suggestion').join(' ');
		expect(shown).toContain('3');
		expect(shown).toContain('87%');
		expect(vision.requests.map((call) => call.path)).toContain('/v1/suggestion');
	});

	it('shows the machine normally when cs71-vision is unreachable, with no suggestion', async () => {
		await vision.close();

		const html = await loadedPage();

		expect(fieldText(html, 'vision-suggestion')).toEqual([]);
		// The machine itself is unaffected: cs71d is still answering normally.
		expect(fieldText(html, 'connection').join(' ')).toContain('Connected');
	});
});

describe('automated accessibility checks (PI-SWQ-002)', () => {
	it('meets WCAG 2.1/2.2 A/AA rules axe-core can evaluate without CSS layout', async () => {
		const html = rendered(snapshot({ active_operation: operation() }), {
			suggestion: {
				slot: 3,
				confidence: 0.87,
				modelVersion: 2,
				suggestedAt: '2026-08-11T12:00:00.000Z',
				primerPresent: null,
				requiresConfirmation: true
			},
			routing: {
				active: true,
				kind: 'fixed',
				startedAt: '2026-08-11T12:00:00.000Z',
				sourceGroup: null,
				legend: []
			}
		});

		const report = await checkAccessibility(html);

		expect(report.violations).toEqual([]);
	});
});

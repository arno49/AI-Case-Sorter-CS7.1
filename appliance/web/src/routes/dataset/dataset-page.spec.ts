/**
 * The dataset review screen as an operator meets it.
 *
 * Rendering is the real page via `svelte/server`. The end-to-end spec goes
 * further: the real load and the real train/activate/rollback actions read
 * and write through the real hook, against a stand-in `cs71-vision` on a
 * real socket.
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
import type {
	AutonomySummary,
	DatasetSummary,
	ModelsSummary,
	SuggestionAccuracy
} from '$lib/dataset';

import Page from './+page.svelte';
import { actions as datasetActions, load as datasetLoad } from './+page.server';
import { handle } from '../../hooks.server';
import { checkAccessibility } from '../accessibility';
import { browser, csrfFor, request, throughHook, type CookieJar } from '../harness';
import { fieldText, focusOrderIsDocumentOrder, visibleText } from '../rendered';

function dataset(overrides: Partial<DatasetSummary> = {}): DatasetSummary {
	return {
		minimumExamplesPerClass: 40,
		classes: [
			{ slot: 3, count: 52, eligible: true },
			{ slot: 5, count: 12, eligible: false }
		],
		trainingReady: true,
		...overrides
	};
}

function models(overrides: Partial<ModelsSummary> = {}): ModelsSummary {
	return {
		activeVersion: 1,
		canRollBack: false,
		candidates: [
			{
				version: 1,
				trainedAt: '2026-08-12T12:00:00.000Z',
				includedClasses: [3, 5],
				excludedClasses: [7],
				accuracyByClass: { 3: 1.0, 5: 0.9 },
				minimumExamplesPerClass: 40,
				trainingExampleCount: 64,
				holdoutExampleCount: 16
			}
		],
		...overrides
	};
}

interface PageData {
	readonly dataset: DatasetSummary | null;
	readonly models: ModelsSummary | null;
	readonly suggestionAccuracy: SuggestionAccuracy | null;
	readonly autonomy: AutonomySummary | null;
	readonly unavailable: string | null;
	readonly canTrain: boolean;
	readonly csrfToken: string;
}

function autonomy(overrides: Partial<AutonomySummary> = {}): AutonomySummary {
	return {
		thresholds: { 3: 0.95 },
		accuracyByClass: {},
		pendingReview: [
			{
				attemptId: 7,
				suggestionId: 42,
				operationId: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
				slot: 3,
				attemptedAt: '2026-08-12T12:07:00.000Z'
			}
		],
		...overrides
	};
}

function rendered(data: PageData, form: unknown = null): string {
	return render(Page as never, { props: { data, form } as never }).body;
}

function pageData(overrides: Partial<PageData> = {}): PageData {
	return {
		dataset: dataset(),
		models: models(),
		suggestionAccuracy: { total: 4, correct: 3, accuracy: 0.75 },
		autonomy: autonomy(),
		unavailable: null,
		canTrain: true,
		csrfToken: 'csrf',
		...overrides
	};
}

describe('what the dataset view shows', () => {
	it('shows each class next to the configured floor', () => {
		const html = rendered(pageData());

		expect(fieldText(html, 'dataset-class-3').join(' ')).toContain('52 examples');
		expect(fieldText(html, 'dataset-class-3').join(' ')).toContain('floor 40');
	});

	it('marks a class below the floor ineligible with the reason shown, not merely absent', () => {
		const html = rendered(pageData());

		const below = fieldText(html, 'dataset-class-5').join(' ');
		expect(below).toContain('12 examples');
		expect(below).toContain('Ineligible');
		expect(below).toContain('Below the 40-example floor');
	});

	it('states readiness before any training control could be offered', () => {
		const html = rendered(pageData());

		expect(fieldText(html, 'dataset-readiness').join(' ')).toContain('cleared the floor');
	});

	it('says no examples are recorded yet, rather than an empty table, when the dataset is empty', () => {
		const html = rendered(pageData({ dataset: dataset({ classes: [], trainingReady: false }) }));

		expect(fieldText(html, 'dataset-readiness').join(' ')).toContain('No examples recorded yet');
	});

	it('shows the servers words when cs71-vision is unavailable, and no facts', () => {
		const html = rendered(
			pageData({
				dataset: null,
				models: null,
				unavailable: 'The classifier service is not answering.'
			})
		);

		expect(visibleText(html)).toContain('not answering');
		expect(fieldText(html, 'dataset-readiness')).toEqual([]);
	});

	it('shows each recorded model version, newest first, with the active one marked', () => {
		const html = rendered(
			pageData({
				models: models({
					activeVersion: 2,
					candidates: [
						{ ...models().candidates[0], version: 1 },
						{ ...models().candidates[0], version: 2 }
					]
				})
			})
		);

		expect(fieldText(html, 'model-2-active')).not.toEqual([]);
		expect(fieldText(html, 'model-1-active')).toEqual([]);
	});

	it('shows a candidates accuracy next to the active models own, before any activate control', () => {
		const html = rendered(pageData());

		expect(html).toContain('This candidate');
		expect(html).toContain('Currently active');
		expect(html).toContain('90%');
	});

	it('says no candidate has been trained yet when the model list is empty', () => {
		const html = rendered(pageData({ models: models({ activeVersion: null, candidates: [] }) }));

		expect(fieldText(html, 'models-empty')).not.toEqual([]);
	});

	it('offers no train, activate or rollback control to an account without vision.train', () => {
		const html = rendered(pageData({ canTrain: false }));

		expect(html).not.toContain('Train a new candidate');
		expect(html).not.toContain('Activate this version');
	});

	it('disables training until at least one class clears the floor', () => {
		const html = rendered(pageData({ dataset: dataset({ trainingReady: false }) }));

		expect(html).toMatch(/disabled[^>]*>\s*Train a new candidate/);
	});

	it('offers no rollback control when there is nothing to roll back to', () => {
		const html = rendered(pageData({ models: models({ canRollBack: false }) }));

		expect(html).not.toContain('Roll back to the previous version');
	});

	it('offers a rollback control when a previous version exists', () => {
		const html = rendered(pageData({ models: models({ canRollBack: true }) }));

		expect(html).toContain('Roll back to the previous version');
	});

	it('shows live suggestion accuracy, kept separate from held-out accuracy', () => {
		const html = rendered(
			pageData({ suggestionAccuracy: { total: 8, correct: 6, accuracy: 0.75 } })
		);

		const shown = fieldText(html, 'suggestion-accuracy').join(' ');
		expect(shown).toContain('75%');
		expect(shown).toContain('6 of 8 sorts');
	});

	it('says nothing has matched yet rather than a bare zero percent', () => {
		const html = rendered(
			pageData({ suggestionAccuracy: { total: 0, correct: 0, accuracy: null } })
		);

		expect(fieldText(html, 'suggestion-accuracy').join(' ')).toContain(
			'No suggestion has been matched'
		);
	});

	it('shows a pending autonomous attempt with correct/incorrect controls, to an account with vision.train', () => {
		const html = rendered(pageData());

		expect(fieldText(html, 'autonomy-pending-7').join(' ')).toContain('Slot 3');
		expect(html).toContain('Correct');
		expect(html).toContain('Incorrect');
	});

	it('offers no review controls to an account without vision.train', () => {
		const html = rendered(pageData({ canTrain: false }));

		expect(html).not.toContain('>Correct<');
		expect(html).not.toContain('>Incorrect<');
	});

	it('says no threshold is configured when autonomy has neither a threshold nor a review', () => {
		const html = rendered(
			pageData({ autonomy: autonomy({ thresholds: {}, accuracyByClass: {}, pendingReview: [] }) })
		);

		expect(fieldText(html, 'autonomy-empty')).not.toEqual([]);
	});

	it('never reports an unreviewed class as a fabricated zero-percent false rate', () => {
		const html = rendered(
			pageData({
				autonomy: autonomy({ thresholds: { 3: 0.95 }, accuracyByClass: {}, pendingReview: [] })
			})
		);

		const shown = fieldText(html, 'autonomy-class-3').join(' ');
		expect(shown).toContain('No autonomous attempt has been reviewed yet');
		expect(shown).not.toContain('0%');
	});
});

describe('reading and acting on the dataset view, end to end', () => {
	let directory: string;
	let vision: FakeDaemon;

	beforeEach(async () => {
		directory = mkdtempSync(join(tmpdir(), 'cs71-dataset-'));
		vision = await startFakeDaemon();
		vision.answerWith((call, response) => {
			if (call.path === '/v1/dataset') {
				replying(200, {
					api_version: 'v1',
					minimum_examples_per_class: 40,
					classes: [{ slot: 3, count: 52, eligible: true }],
					training_ready: true
				})(call, response);
			} else if (call.path === '/v1/models') {
				replying(200, {
					api_version: 'v1',
					active_version: 1,
					can_roll_back: true,
					candidates: [
						{
							version: 1,
							trained_at: '2026-08-12T12:00:00.000Z',
							included_classes: [3],
							excluded_classes: [],
							accuracy_by_class: { '3': 1.0 },
							minimum_examples_per_class: 40,
							training_example_count: 10,
							holdout_example_count: 2
						}
					]
				})(call, response);
			} else if (call.path === '/v1/train') {
				replying(200, { api_version: 'v1', started: true })(call, response);
			} else if (call.path === '/v1/models/1/activate') {
				replying(200, {
					api_version: 'v1',
					active_version: 1,
					activated_at: '2026-08-12T12:05:00.000Z'
				})(call, response);
			} else if (call.path === '/v1/rollback') {
				replying(200, {
					api_version: 'v1',
					active_version: 1,
					activated_at: '2026-08-12T12:06:00.000Z'
				})(call, response);
			} else if (call.path === '/v1/suggestion-accuracy') {
				replying(200, { api_version: 'v1', total: 4, correct: 3, accuracy: 0.75 })(call, response);
			} else if (call.path === '/v1/autonomy') {
				replying(200, {
					api_version: 'v1',
					thresholds: { '3': 0.95 },
					accuracy_by_class: {},
					pending_review: [
						{
							attempt_id: 7,
							suggestion_id: 42,
							operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
							slot: 3,
							attempted_at: '2026-08-12T12:07:00.000Z'
						}
					]
				})(call, response);
			} else if (call.path === '/v1/autonomous-reviews') {
				replying(200, {
					api_version: 'v1',
					attempt_id: 7,
					correct: true,
					reviewed_at: '2026-08-12T12:08:00.000Z'
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

	it('reads the dataset and models summaries through the real hook', async () => {
		const cookies = await signedIn('viewer');

		const opened = request('/dataset', cookies, { routeId: '/dataset' });
		await throughHook(opened, handle);
		const data = await datasetLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data } as never }).body;

		expect(fieldText(html, 'dataset-class-3').join(' ')).toContain('52 examples');
		expect(fieldText(html, 'model-1').join(' ')).toContain('Version 1');
		const paths = vision.requests.map((call) => call.path);
		expect(paths).toEqual(expect.arrayContaining(['/v1/dataset', '/v1/models']));
	});

	it('reports cs71-vision as unavailable rather than failing the page', async () => {
		vision.answerWith((_call, response) => {
			response.destroy();
		});
		const cookies = await signedIn('viewer');

		const opened = request('/dataset', cookies, { routeId: '/dataset' });
		await throughHook(opened, handle);
		const data = await datasetLoad(opened as unknown as ServerLoadEvent);

		expect((data as PageData).dataset).toBeNull();
		expect((data as PageData).models).toBeNull();
		expect((data as PageData).unavailable).toBeTruthy();
	});

	it('does not offer training to a viewer', async () => {
		const cookies = await signedIn('viewer');

		const opened = request('/dataset', cookies, { routeId: '/dataset' });
		await throughHook(opened, handle);
		const data = await datasetLoad(opened as unknown as ServerLoadEvent);

		expect((data as PageData).canTrain).toBe(false);
	});

	it('lets an operator trigger a training run, and audits it', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		const answer = await datasetActions.train(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'train', started: true });
		expect(vision.requests.some((call) => call.path === '/v1/train')).toBe(true);
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.train',
			outcome: 'accepted'
		});
	});

	it('lets an operator activate a candidate, and audits it', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies), version: '1' },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		const answer = await datasetActions.activate(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'activate', activeVersion: 1 });
		expect(vision.requests.some((call) => call.path === '/v1/models/1/activate')).toBe(true);
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.activate',
			outcome: 'accepted'
		});
	});

	it('refuses an activate request with an invalid version, sending nothing', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies), version: 'not-a-number' },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		const answer = await datasetActions.activate(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ status: 400, data: { control: 'activate' } });
		expect(vision.requests.some((call) => call.path.includes('/activate'))).toBe(false);
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.activate',
			outcome: 'refused'
		});
	});

	it('lets an operator roll back, and audits it', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		const answer = await datasetActions.rollback(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'rollback', activeVersion: 1 });
		expect(vision.requests.some((call) => call.path === '/v1/rollback')).toBe(true);
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.rollback',
			outcome: 'accepted'
		});
	});

	it('lets an operator record a verdict on an autonomous attempt, and audits it', async () => {
		const cookies = await signedIn('operator');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies), attempt_id: '7', correct: 'true' },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		const answer = await datasetActions.reviewAutonomousAttempt(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ control: 'reviewAutonomousAttempt', attemptId: 7 });
		expect(vision.requests.some((call) => call.path === '/v1/autonomous-reviews')).toBe(true);
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.reviewAutonomousAttempt',
			outcome: 'accepted'
		});
	});

	it('refuses a viewer permission to review an autonomous attempt, before anything reaches cs71-vision', async () => {
		const cookies = await signedIn('viewer');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies), attempt_id: '7', correct: 'true' },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		await expect(
			datasetActions.reviewAutonomousAttempt(pressed as unknown as RequestEvent)
		).rejects.toMatchObject({ status: 403 });
		expect(vision.requests.some((call) => call.path === '/v1/autonomous-reviews')).toBe(false);
	});

	it('refuses a viewer permission to train, before anything reaches cs71-vision', async () => {
		const cookies = await signedIn('viewer');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		await expect(datasetActions.train(pressed as unknown as RequestEvent)).rejects.toMatchObject({
			status: 403
		});
		expect(vision.requests.some((call) => call.path === '/v1/train')).toBe(false);
	});

	it('audits a refusal when cs71-vision itself refuses the request', async () => {
		vision.answerWith((call, response) => {
			if (call.path === '/v1/dataset' || call.path === '/v1/models') {
				replying(200, {
					api_version: 'v1',
					minimum_examples_per_class: 40,
					classes: [],
					training_ready: false,
					active_version: null,
					can_roll_back: false,
					candidates: []
				})(call, response);
				return;
			}
			replying(400, { code: 'VALIDATION_FAILED', message: 'no previous version' })(call, response);
		});
		const cookies = await signedIn('operator');
		const pressed = request('/dataset', cookies, {
			form: { [CSRF_FIELD]: csrfFor(cookies) },
			routeId: '/dataset'
		});
		await throughHook(pressed, handle);

		const answer = await datasetActions.rollback(pressed as unknown as RequestEvent);

		expect(answer).toMatchObject({ status: 400 });
		const { database } = webRuntime();
		expect(recentAudit(database, 1)[0]).toMatchObject({
			action: 'vision.rollback',
			outcome: 'refused'
		});
	});
});

describe('automated accessibility checks (PI-SWQ-002)', () => {
	it('meets WCAG 2.1/2.2 A/AA rules axe-core can evaluate without CSS layout', async () => {
		const html = rendered(pageData());

		const report = await checkAccessibility(html);

		expect(report.violations).toEqual([]);
	});

	it('declares no tabindex that could pull a control ahead of document order', () => {
		expect(focusOrderIsDocumentOrder(rendered(pageData()))).toBe(true);
	});
});

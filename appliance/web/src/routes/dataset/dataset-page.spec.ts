/**
 * The dataset review screen as an operator meets it.
 *
 * Rendering is the real page via `svelte/server`. The end-to-end spec goes
 * further: the real load reads cs71-vision's dataset api through the real
 * hook from a stand-in service on a real socket.
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
import type { DatasetSummary } from '$lib/dataset';

import Page from './+page.svelte';
import { load as datasetLoad } from './+page.server';
import { handle } from '../../hooks.server';
import { browser, request, throughHook } from '../harness';
import { fieldText, visibleText } from '../rendered';

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

interface PageData {
	readonly dataset: DatasetSummary | null;
	readonly unavailable: string | null;
}

function rendered(data: PageData): string {
	return render(Page as never, { props: { data } as never }).body;
}

describe('what the dataset view shows', () => {
	it('shows each class next to the configured floor', () => {
		const html = rendered({ dataset: dataset(), unavailable: null });

		expect(fieldText(html, 'dataset-class-3').join(' ')).toContain('52 examples');
		expect(fieldText(html, 'dataset-class-3').join(' ')).toContain('floor 40');
	});

	it('marks a class below the floor ineligible with the reason shown, not merely absent', () => {
		const html = rendered({ dataset: dataset(), unavailable: null });

		const below = fieldText(html, 'dataset-class-5').join(' ');
		expect(below).toContain('12 examples');
		expect(below).toContain('Ineligible');
		expect(below).toContain('Below the 40-example floor');
	});

	it('states readiness before any training control could be offered', () => {
		const html = rendered({ dataset: dataset(), unavailable: null });

		expect(fieldText(html, 'dataset-readiness').join(' ')).toContain('cleared the floor');
	});

	it('says no examples are recorded yet, rather than an empty table, when the dataset is empty', () => {
		const html = rendered({
			dataset: dataset({ classes: [], trainingReady: false }),
			unavailable: null
		});

		expect(fieldText(html, 'dataset-readiness').join(' ')).toContain('No examples recorded yet');
	});

	it('shows the servers words when cs71-vision is unavailable, and no facts', () => {
		const html = rendered({
			dataset: null,
			unavailable: 'The classifier service is not answering.'
		});

		expect(visibleText(html)).toContain('not answering');
		expect(fieldText(html, 'dataset-readiness')).toEqual([]);
	});
});

describe('reading the dataset view, end to end', () => {
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

	it('reads the dataset summary through the real hook', async () => {
		const { config, database } = webRuntime();
		const user = await createUser(
			database,
			{ username: 'viewer', password: PASSWORD, role: 'viewer' },
			new Date()
		);
		const cookies = browser();
		startSession(database, cookies, user.userId, config.profile, new Date());

		const opened = request('/dataset', cookies, { routeId: '/dataset' });
		await throughHook(opened, handle);
		const data = await datasetLoad(opened as unknown as ServerLoadEvent);
		const html = render(Page as never, { props: { data } as never }).body;

		expect(fieldText(html, 'dataset-class-3').join(' ')).toContain('52 examples');
		expect(vision.requests.map((call) => call.path)).toContain('/v1/dataset');
	});

	it('reports cs71-vision as unavailable rather than failing the page', async () => {
		vision.answerWith((_call, response) => {
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

		const opened = request('/dataset', cookies, { routeId: '/dataset' });
		await throughHook(opened, handle);
		const data = await datasetLoad(opened as unknown as ServerLoadEvent);

		expect((data as PageData).dataset).toBeNull();
		expect((data as PageData).unavailable).toBeTruthy();
	});
});

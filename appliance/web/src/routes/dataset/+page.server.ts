/**
 * The dataset review screen: per-class example counts against the training
 * floor (PI-VISION-003).
 *
 * A read, same shape as `/system`'s own load - there is nothing here to
 * submit, so there is no idempotency key, no generation to match and nothing
 * for the web audit to record. The only difference from `/system` is which
 * service answers: this reads `cs71-vision`'s dataset api, not `cs71d`.
 */

import type { ServerLoad } from '@sveltejs/kit';

import type { DatasetSummary } from '$lib/dataset';
import { webRuntime } from '$lib/server/runtime';
import { safeResponseFor } from '$lib/server/vision';

export const load: ServerLoad = async ({ locals }) => {
	// The hook has already refused an unauthenticated request and checked that
	// this role may read the machine.
	const { vision } = webRuntime();

	let dataset: DatasetSummary | null = null;
	let unavailable: string | null = null;
	try {
		dataset = await vision.datasetSummary();
	} catch (error) {
		unavailable = safeResponseFor(error).message;
		reportToServerLog(error, locals.requestId);
	}

	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		dataset,
		unavailable
	};
};

/**
 * cs71-vision's own words, kept where an engineer can find them.
 *
 * They never reach a page: the operator gets wording this workspace owns,
 * the same discipline `/system`'s load applies to a `DaemonError`.
 */
function reportToServerLog(error: unknown, requestId: string): void {
	const description = error instanceof Error ? error.message : String(error);
	console.error(`request ${requestId}: ${description}`);
}

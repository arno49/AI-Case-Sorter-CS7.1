/**
 * The durable operation history: what the machine service recorded, further
 * back than the dashboard's single active operation.
 *
 * This is a read, same as the dashboard's snapshot. It carries no action:
 * there is nothing here to submit, so there is no idempotency key, no
 * generation to match, and nothing for the web audit to record. A filter is a
 * query string, not a form post, so a bookmarked or shared URL reproduces the
 * same page.
 *
 * A filter value this workspace does not recognise is dropped rather than
 * sent to the daemon: a mistyped or stale query string shows the unfiltered
 * page instead of failing the whole screen.
 */

import type { ServerLoad } from '@sveltejs/kit';

import { STATE_OPTIONS, TYPE_OPTIONS, type HistoryFilter } from '$lib/operation-history';
import type { OperationHistoryQuery } from '$lib/server/daemon/client';
import { safeResponseFor } from '$lib/server/daemon/errors';
import { webRuntime } from '$lib/server/runtime';
import type { OperationPage, OperationState, OperationType } from '$lib/machine';

const STATE_VALUES = new Set<string>(STATE_OPTIONS);
const TYPE_VALUES = new Set<string>(TYPE_OPTIONS);
const CURSOR_MAXIMUM_LENGTH = 256;
const LIMIT_MAXIMUM = 100;

export const load: ServerLoad = async ({ locals, url }) => {
	// The hook has already refused an unauthenticated request and checked that
	// this role may read the machine.
	const { daemon } = webRuntime();

	const filter = filterFrom(url.searchParams);
	const query = queryFrom(filter, url.searchParams);

	let page: OperationPage | null = null;
	let unavailable: string | null = null;
	try {
		page = await daemon.operations(query);
	} catch (error) {
		unavailable = safeResponseFor(error).message;
		reportToServerLog(error, locals.requestId);
	}

	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		page,
		unavailable,
		filter
	};
};

function filterFrom(params: URLSearchParams): HistoryFilter {
	const state = params.get('state');
	const type = params.get('type');
	return {
		state: state !== null && STATE_VALUES.has(state) ? (state as OperationState) : null,
		type: type !== null && TYPE_VALUES.has(type) ? (type as OperationType) : null
	};
}

function queryFrom(filter: HistoryFilter, params: URLSearchParams): OperationHistoryQuery {
	const cursor = params.get('cursor');
	const limit = params.get('limit');
	return {
		...(filter.state === null ? {} : { state: filter.state }),
		...(filter.type === null ? {} : { type: filter.type }),
		...(cursor !== null && cursor.length > 0 && cursor.length <= CURSOR_MAXIMUM_LENGTH
			? { cursor }
			: {}),
		...(limit !== null &&
		/^\d{1,3}$/.test(limit) &&
		Number(limit) >= 1 &&
		Number(limit) <= LIMIT_MAXIMUM
			? { limit: Number(limit) }
			: {})
	};
}

/**
 * The daemon's own words, kept where an engineer can find them.
 *
 * They never reach a page: the operator gets wording this workspace owns. The
 * line carries no credential, cookie or form body — a `DaemonError` message is
 * built from the code, the request id and the daemon's sanitized message.
 */
function reportToServerLog(error: unknown, requestId: string): void {
	const description = error instanceof Error ? error.message : String(error);
	console.error(`request ${requestId}: ${description}`);
}

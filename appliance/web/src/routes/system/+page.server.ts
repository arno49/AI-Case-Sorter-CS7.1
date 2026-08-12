/**
 * The system view: versions, journal evidence and DTR-gate status.
 *
 * A read, same as the dashboard's snapshot — there is nothing here to submit,
 * so there is no idempotency key, no generation to match and nothing for the
 * web audit to record. Both reads come from the same daemon, so a failure of
 * either is reported as one `unavailable` rather than two.
 */

import type { ServerLoad } from '@sveltejs/kit';

import { safeResponseFor } from '$lib/server/daemon/errors';
import { webRuntime } from '$lib/server/runtime';
import type { MachineSnapshot, System } from '$lib/machine';

export const load: ServerLoad = async ({ locals }) => {
	// The hook has already refused an unauthenticated request and checked that
	// this role may read the machine.
	const { daemon } = webRuntime();

	let snapshot: MachineSnapshot | null = null;
	let system: System | null = null;
	let unavailable: string | null = null;
	try {
		[snapshot, system] = await Promise.all([daemon.snapshot(), daemon.system()]);
	} catch (error) {
		unavailable = safeResponseFor(error).message;
		reportToServerLog(error, locals.requestId);
	}

	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		snapshot,
		system,
		unavailable
	};
};

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

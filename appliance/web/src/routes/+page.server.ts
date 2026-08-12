/**
 * The dashboard, and the one command wired to the machine so far.
 *
 * The page reads the daemon's snapshot; the action asks the daemon to stop.
 * Both go through `$lib/server/daemon`, so the browser never learns a socket
 * path, a device or a protocol word — it submits an intent and is told what
 * this workspace decided to say about the answer.
 *
 * A daemon that is not answering makes the page report that, not fail. An
 * operator whose machine has gone quiet still needs the screen, and the stop
 * control has to stay on it.
 */

import { fail, type Actions, type ServerLoad } from '@sveltejs/kit';

import { recordAudit } from '$lib/server/audit';
import { requireCapability } from '$lib/server/auth/authorization';
import { capabilitiesFor } from '$lib/server/auth/capabilities';
import { actorFor } from '$lib/server/daemon/client';
import { DaemonError, safeResponseFor } from '$lib/server/daemon/errors';
import { webRuntime } from '$lib/server/runtime';

/** What this workspace calls the intent, in audit entries and nowhere on the wire. */
const STOP_ACTION = 'machine.stop';

export const load: ServerLoad = async ({ locals }) => {
	// The hook has already refused an unauthenticated request and checked that
	// this role may read the machine.
	const { daemon } = webRuntime();

	let snapshot = null;
	let unavailable: string | null = null;
	try {
		snapshot = await daemon.snapshot();
	} catch (error) {
		unavailable = safeResponseFor(error).message;
		reportToServerLog(error, locals.requestId);
	}

	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		capabilities: locals.user === null ? [] : capabilitiesFor(locals.user.role),
		csrfToken: locals.csrfToken,
		snapshot,
		unavailable
	};
};

export const actions: Actions = {
	/**
	 * Software stop. Not an emergency stop, and never presented as one.
	 *
	 * Authorization happens here, next to the effect, rather than being inferred
	 * from the fact that the page rendered a button.
	 */
	stop: async ({ locals }) => {
		const user = requireCapability(locals.user, 'machine.stop');
		const { daemon, database } = webRuntime();
		const now = new Date();

		try {
			// No idempotency key from the browser and none reused: deduplication is
			// for a resubmitted intent, and a second press of stop is a second
			// intent. A replayed key could turn a stop into a no-op.
			const accepted = await daemon.stop({ actor: actorFor(user) });
			recordAudit(
				database,
				{
					userId: user.userId,
					role: user.role,
					action: STOP_ACTION,
					outcome: 'accepted',
					requestId: locals.requestId,
					operationId: accepted.operation_id
				},
				now
			);
			return {
				operationId: accepted.operation_id,
				state: accepted.state,
				generation: accepted.generation
			};
		} catch (error) {
			const daemonError = error instanceof DaemonError ? error : undefined;
			recordAudit(
				database,
				{
					userId: user.userId,
					role: user.role,
					action: STOP_ACTION,
					outcome: daemonError?.kind === 'rejected' ? 'refused' : 'failed',
					requestId: locals.requestId,
					daemonCode: daemonError?.code ?? null,
					daemonRequestId: daemonError?.requestId ?? null
				},
				now
			);
			reportToServerLog(error, locals.requestId);

			const { status, message } = safeResponseFor(error);
			return fail(status, { error: message });
		}
	}
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

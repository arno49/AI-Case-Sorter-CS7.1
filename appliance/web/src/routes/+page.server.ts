/**
 * The dashboard and the manual commands wired to the machine.
 *
 * The page reads the daemon's snapshot; the actions ask the daemon to stop,
 * connect, home, sort or feed. Everything goes through `$lib/server/daemon`,
 * so the browser never learns a socket path, a device or a protocol word — it
 * submits an intent and is told what this workspace decided to say about the
 * answer.
 *
 * A manual command is not a bare button press. The form carries the snapshot
 * generation the operator was looking at and an idempotency key minted for
 * that render, so a decision made against a machine that has since changed is
 * refused, and a resubmitted form is the same command rather than a second
 * one.
 *
 * A daemon that is not answering makes the page report that, not fail. An
 * operator whose machine has gone quiet still needs the screen, and the stop
 * control has to stay on it.
 */

import { fail, type Actions, type RequestEvent, type ServerLoad } from '@sveltejs/kit';

import { recordAudit } from '$lib/server/audit';
import { requireCapability } from '$lib/server/auth/authorization';
import { capabilitiesFor } from '$lib/server/auth/capabilities';
import {
	actorFor,
	InvalidCommandError,
	newIdempotencyKey,
	type CommandContext,
	type DaemonClient,
	type HomeTarget,
	type OperationAccepted
} from '$lib/server/daemon/client';
import { DaemonError, safeResponseFor } from '$lib/server/daemon/errors';
import { webRuntime } from '$lib/server/runtime';

/** What this workspace calls the intent, in audit entries and nowhere on the wire. */
const STOP_ACTION = 'machine.stop';

/** Our own form failed our own checks; nothing was sent to the daemon. */
const INVALID_MESSAGE = 'That request was rejected as invalid.';

type ManualControl = 'connect' | 'home' | 'sort' | 'feed';

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

	const capabilities = locals.user === null ? [] : capabilitiesFor(locals.user.role);

	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		capabilities,
		csrfToken: locals.csrfToken,
		snapshot,
		unavailable,
		// One key per command form, fresh for this render: resubmitting the form
		// this response carries is the same command, and the next render is a new
		// intent. Minted only for a role that can use them.
		commandKeys: capabilities.includes('machine.operate')
			? {
					connect: newIdempotencyKey(),
					home: newIdempotencyKey(),
					sort: newIdempotencyKey(),
					feed: newIdempotencyKey()
				}
			: null
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
				control: 'stop' as const,
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
			return fail(status, { control: 'stop' as const, error: message });
		}
	},

	connect: (event) => operate(event, 'connect', (daemon, context) => daemon.connect(context)),

	home: (event) =>
		operate(event, 'home', (daemon, context, form) => daemon.home(context, homeTargetField(form))),

	sort: (event) =>
		operate(event, 'sort', (daemon, context, form) =>
			daemon.sort(context, integerField(form, 'slot'))
		),

	feed: (event) => operate(event, 'feed', (daemon, context) => daemon.feed(context))
};

/**
 * One manual command, from form to daemon to audit.
 *
 * The form's `generation` is the snapshot the operator decided against and its
 * `idempotency_key` is the identity of this intent; both go to the daemon as
 * headers, so a stale page is refused there and a resubmission deduplicates
 * there. A form that fails this workspace's own checks is refused without
 * anything being sent, and that refusal is audited too: an attempt the
 * operator made is an attempt on the record.
 */
async function operate(
	event: RequestEvent,
	control: ManualControl,
	command: (
		daemon: DaemonClient,
		context: CommandContext,
		form: FormData
	) => Promise<OperationAccepted>
) {
	const user = requireCapability(event.locals.user, 'machine.operate');
	const { daemon, database } = webRuntime();
	const now = new Date();
	const action = `machine.${control}`;

	try {
		const form = await event.request.formData();
		const context: CommandContext = {
			actor: actorFor(user),
			generation: integerField(form, 'generation'),
			idempotencyKey: stringField(form, 'idempotency_key')
		};
		const accepted = await command(daemon, context, form);
		recordAudit(
			database,
			{
				userId: user.userId,
				role: user.role,
				action,
				outcome: 'accepted',
				requestId: event.locals.requestId,
				operationId: accepted.operation_id
			},
			now
		);
		return {
			control,
			operationId: accepted.operation_id,
			state: accepted.state,
			generation: accepted.generation
		};
	} catch (error) {
		const daemonError = error instanceof DaemonError ? error : undefined;
		const invalid = error instanceof InvalidCommandError;
		recordAudit(
			database,
			{
				userId: user.userId,
				role: user.role,
				action,
				outcome: invalid || daemonError?.kind === 'rejected' ? 'refused' : 'failed',
				requestId: event.locals.requestId,
				daemonCode: daemonError?.code ?? null,
				daemonRequestId: daemonError?.requestId ?? null
			},
			now
		);
		reportToServerLog(error, event.locals.requestId);

		if (invalid) {
			return fail(400, { control, error: INVALID_MESSAGE });
		}
		const { status, message } = safeResponseFor(error);
		return fail(status, { control, error: message });
	}
}

function stringField(form: FormData, name: string): string {
	const value = form.get(name);
	if (typeof value !== 'string' || value.length === 0) {
		throw new InvalidCommandError(`the ${name} field is required`);
	}
	return value;
}

function integerField(form: FormData, name: string): number {
	const value = form.get(name);
	if (typeof value !== 'string' || !/^\d{1,15}$/.test(value)) {
		throw new InvalidCommandError(`the ${name} field must be a non-negative integer`);
	}
	return Number(value);
}

function homeTargetField(form: FormData): HomeTarget {
	const value = form.get('target');
	if (value === 'feeder' || value === 'sorter' || value === 'both') {
		return value;
	}
	throw new InvalidCommandError('the home target must be feeder, sorter or both');
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

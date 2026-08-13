/**
 * The routing profile screen (PI-VISION-009, ADR-0013): select how
 * manufacturer classes map onto physical chutes for the run about to
 * start, and see the live chute<->class legend for the one currently
 * active.
 *
 * The read half is the same shape as `/dataset`'s own load: nothing to
 * submit, so a `cs71-vision` outage becomes one `unavailable` message
 * rather than a page error. The write half mirrors `/dataset`'s own
 * actions - `requireCapability` next to the effect, `recordAudit` on every
 * outcome, form fields validated before anything is sent - gated on
 * `machine.operate` rather than `vision.train`: a routing choice shapes
 * where a live sort actually lands, the same operational weight as
 * connect/home/sort/feed, not a model-quality decision.
 */

import { fail, type Actions, type ServerLoad } from '@sveltejs/kit';

import type { RoutingState } from '$lib/routing';
import { recordAudit } from '$lib/server/audit';
import { requireCapability } from '$lib/server/auth/authorization';
import { can } from '$lib/server/auth/capabilities';
import type { WebDatabase } from '$lib/server/auth/database';
import type { UserRecord } from '$lib/server/auth/users';
import { webRuntime } from '$lib/server/runtime';
import {
	InvalidCommandError,
	VisionError,
	safeResponseFor,
	type RoutingProfileRequest
} from '$lib/server/vision';

const INVALID_MESSAGE = 'That request was rejected as invalid.';

export const load: ServerLoad = async ({ locals }) => {
	// The hook has already refused an unauthenticated request and checked that
	// this role may read the machine.
	const { vision } = webRuntime();

	let routing: RoutingState | null = null;
	let unavailable: string | null = null;
	try {
		routing = await vision.routing();
	} catch (error) {
		unavailable = safeResponseFor(error).message;
		reportToServerLog(error, locals.requestId);
	}

	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		csrfToken: locals.csrfToken,
		canOperate: locals.user !== null && can(locals.user.role, 'machine.operate'),
		routing,
		unavailable
	};
};

export const actions: Actions = {
	startFixed: async (event) => {
		const user = requireCapability(event.locals.user, 'machine.operate');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			const form = await event.request.formData();
			const profile: RoutingProfileRequest = {
				kind: 'fixed',
				classToSlot: classSlotField(form, 'class_to_slot'),
				overflowSlot: integerField(form, 'overflow_slot')
			};
			const result = await vision.startRouting(profile);
			auditRouting(database, user, 'accepted', event.locals.requestId, now);
			return { control: 'start', kind: result.kind };
		} catch (error) {
			return failRoutingAction('start', user, error, event.locals.requestId, now);
		}
	},

	startDynamic: async (event) => {
		const user = requireCapability(event.locals.user, 'machine.operate');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			const form = await event.request.formData();
			const profile: RoutingProfileRequest = {
				kind: 'dynamic',
				availableSlots: slotListField(form, 'available_slots')
			};
			const result = await vision.startRouting(profile);
			auditRouting(database, user, 'accepted', event.locals.requestId, now);
			return { control: 'start', kind: result.kind };
		} catch (error) {
			return failRoutingAction('start', user, error, event.locals.requestId, now);
		}
	},

	startTwoPass: async (event) => {
		const user = requireCapability(event.locals.user, 'machine.operate');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			const form = await event.request.formData();
			const sourceGroupRaw = form.get('source_group');
			const profile: RoutingProfileRequest = {
				kind: 'two_pass',
				classToSlot: classSlotField(form, 'class_to_slot'),
				overflowSlot: integerField(form, 'overflow_slot'),
				...(typeof sourceGroupRaw === 'string' && sourceGroupRaw !== ''
					? { sourceGroup: integerField(form, 'source_group') }
					: {})
			};
			const result = await vision.startRouting(profile);
			auditRouting(database, user, 'accepted', event.locals.requestId, now);
			return { control: 'start', kind: result.kind };
		} catch (error) {
			return failRoutingAction('start', user, error, event.locals.requestId, now);
		}
	},

	stop: async ({ locals }) => {
		const user = requireCapability(locals.user, 'machine.operate');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			await vision.stopRouting();
			auditRouting(database, user, 'accepted', locals.requestId, now);
			return { control: 'stop' };
		} catch (error) {
			return failRoutingAction('stop', user, error, locals.requestId, now);
		}
	}
};

function auditRouting(
	database: WebDatabase,
	user: UserRecord,
	outcome: 'accepted' | 'refused' | 'failed',
	requestId: string,
	now: Date
): void {
	recordAudit(
		database,
		{ userId: user.userId, role: user.role, action: 'vision.routing', outcome, requestId },
		now
	);
}

/**
 * One routing action's failure, from catch to audit to a page-safe answer.
 *
 * A refusal is audited too, the same reasoning `/dataset`'s own actions
 * use: an attempt the operator made is an attempt on the record, whether
 * cs71-vision accepted it or not.
 */
function failRoutingAction(
	control: string,
	user: UserRecord,
	error: unknown,
	requestId: string,
	now: Date
) {
	const { database } = webRuntime();
	const invalid = error instanceof InvalidCommandError;
	const visionError = error instanceof VisionError ? error : undefined;
	auditRouting(
		database,
		user,
		invalid || visionError?.kind === 'rejected' ? 'refused' : 'failed',
		requestId,
		now
	);
	reportToServerLog(error, requestId);

	if (invalid) {
		return fail(400, { control, error: INVALID_MESSAGE });
	}
	const { status, message } = safeResponseFor(error);
	return fail(status, { control, error: message });
}

/** `"12:3, 45:5"` -> `{12: 3, 45: 5}` - one class:slot pair per line or comma. */
function classSlotField(form: FormData, name: string): Record<number, number> {
	const raw = form.get(name);
	if (typeof raw !== 'string' || !raw.trim()) {
		throw new InvalidCommandError(`the ${name} field must carry at least one class:slot pair`);
	}
	const result: Record<number, number> = {};
	for (const entry of splitEntries(raw)) {
		const match = /^(\d{1,15}):(\d{1,15})$/.exec(entry);
		if (!match) {
			throw new InvalidCommandError(`"${entry}" is not a class:slot pair`);
		}
		result[Number(match[1])] = Number(match[2]);
	}
	return result;
}

/** `"1, 2, 3"` -> `[1, 2, 3]` - one chute number per line or comma. */
function slotListField(form: FormData, name: string): number[] {
	const raw = form.get(name);
	if (typeof raw !== 'string' || !raw.trim()) {
		throw new InvalidCommandError(`the ${name} field must carry at least one chute number`);
	}
	return splitEntries(raw).map((entry) => {
		if (!/^\d{1,15}$/.test(entry)) {
			throw new InvalidCommandError(`"${entry}" is not a chute number`);
		}
		return Number(entry);
	});
}

function splitEntries(raw: string): string[] {
	return raw
		.split(/[,\n]+/)
		.map((entry) => entry.trim())
		.filter((entry) => entry.length > 0);
}

function integerField(form: FormData, name: string): number {
	const value = form.get(name);
	if (typeof value !== 'string' || !/^\d{1,15}$/.test(value)) {
		throw new InvalidCommandError(`the ${name} field must be a non-negative integer`);
	}
	return Number(value);
}

/**
 * cs71-vision's own words, kept where an engineer can find them.
 *
 * They never reach a page: the operator gets wording this workspace owns,
 * the same discipline `/dataset`'s load applies to a `VisionError`.
 */
function reportToServerLog(error: unknown, requestId: string): void {
	const description = error instanceof Error ? error.message : String(error);
	console.error(`request ${requestId}: ${description}`);
}

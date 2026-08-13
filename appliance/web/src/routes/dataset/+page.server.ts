/**
 * The dataset review screen: per-class example counts against the training
 * floor (PI-VISION-003), the recorded model versions with train/activate/
 * rollback controls (PI-VISION-004/005), live suggestion accuracy
 * (PI-VISION-006) - separate evidence from any candidate's training-time
 * held-out accuracy, since it is measured against real operator decisions
 * after activation, not a held-out split of the training data - and the
 * autonomous-sort review queue (PI-VISION-008): the false-autonomous-sort
 * rate ADR-0013 requires be recorded before a class's threshold may be
 * lowered has no ground truth to infer from, so it is only ever a human's
 * own recorded verdict on one attempt at a time.
 *
 * The read half is the same shape as `/system`'s own load: nothing to
 * submit, so all three `cs71-vision` reads share one `unavailable` message
 * the way `/system` unifies its own two reads into one. The write half
 * mirrors `/` dashboard's manual-command actions - `requireCapability` next
 * to the effect, `recordAudit` on every outcome, a form's own field
 * validated before anything is sent - except there is no generation or
 * idempotency key here: `cs71-vision` has no optimistic-concurrency model to
 * match, because none of these actions touch machine state.
 */

import { fail, type Actions, type ServerLoad } from '@sveltejs/kit';

import type {
	AutonomySummary,
	DatasetSummary,
	ModelsSummary,
	SuggestionAccuracy
} from '$lib/dataset';
import { recordAudit } from '$lib/server/audit';
import { requireCapability } from '$lib/server/auth/authorization';
import { can } from '$lib/server/auth/capabilities';
import type { UserRecord } from '$lib/server/auth/users';
import { webRuntime } from '$lib/server/runtime';
import { InvalidCommandError, VisionError, safeResponseFor } from '$lib/server/vision';

const INVALID_MESSAGE = 'That request was rejected as invalid.';

export const load: ServerLoad = async ({ locals }) => {
	// The hook has already refused an unauthenticated request and checked that
	// this role may read the machine.
	const { vision } = webRuntime();

	let dataset: DatasetSummary | null = null;
	let models: ModelsSummary | null = null;
	let suggestionAccuracy: SuggestionAccuracy | null = null;
	let autonomy: AutonomySummary | null = null;
	let unavailable: string | null = null;
	try {
		[dataset, models, suggestionAccuracy, autonomy] = await Promise.all([
			vision.datasetSummary(),
			vision.models(),
			vision.suggestionAccuracy(),
			vision.autonomy()
		]);
	} catch (error) {
		unavailable = safeResponseFor(error).message;
		reportToServerLog(error, locals.requestId);
	}

	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		csrfToken: locals.csrfToken,
		canTrain: locals.user !== null && can(locals.user.role, 'vision.train'),
		dataset,
		models,
		suggestionAccuracy,
		autonomy,
		unavailable
	};
};

export const actions: Actions = {
	train: async ({ locals }) => {
		const user = requireCapability(locals.user, 'vision.train');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			const result = await vision.train();
			recordAudit(
				database,
				{
					userId: user.userId,
					role: user.role,
					action: 'vision.train',
					outcome: 'accepted',
					requestId: locals.requestId
				},
				now
			);
			return { control: 'train', started: result.started };
		} catch (error) {
			return failVisionAction('train', user, error, locals.requestId, now);
		}
	},

	activate: async (event) => {
		const user = requireCapability(event.locals.user, 'vision.train');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			const form = await event.request.formData();
			const version = integerField(form, 'version');
			const result = await vision.activate(version);
			recordAudit(
				database,
				{
					userId: user.userId,
					role: user.role,
					action: 'vision.activate',
					outcome: 'accepted',
					requestId: event.locals.requestId
				},
				now
			);
			return { control: 'activate', activeVersion: result.activeVersion };
		} catch (error) {
			return failVisionAction('activate', user, error, event.locals.requestId, now);
		}
	},

	rollback: async ({ locals }) => {
		const user = requireCapability(locals.user, 'vision.train');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			const result = await vision.rollback();
			recordAudit(
				database,
				{
					userId: user.userId,
					role: user.role,
					action: 'vision.rollback',
					outcome: 'accepted',
					requestId: locals.requestId
				},
				now
			);
			return { control: 'rollback', activeVersion: result.activeVersion };
		} catch (error) {
			return failVisionAction('rollback', user, error, locals.requestId, now);
		}
	},

	// PI-VISION-008: recording a human verdict on an autonomous sort never
	// touches machine motion either - it is model-quality evidence, the
	// same reasoning ADR-0013 already uses to grant vision.train to
	// operator rather than reserving it to administrator.
	reviewAutonomousAttempt: async (event) => {
		const user = requireCapability(event.locals.user, 'vision.train');
		const { vision, database } = webRuntime();
		const now = new Date();

		try {
			const form = await event.request.formData();
			const attemptId = integerField(form, 'attempt_id');
			const correct = booleanField(form, 'correct');
			const result = await vision.reviewAutonomousAttempt(attemptId, correct);
			recordAudit(
				database,
				{
					userId: user.userId,
					role: user.role,
					action: 'vision.reviewAutonomousAttempt',
					outcome: 'accepted',
					requestId: event.locals.requestId
				},
				now
			);
			return { control: 'reviewAutonomousAttempt', attemptId: result.attemptId };
		} catch (error) {
			return failVisionAction('reviewAutonomousAttempt', user, error, event.locals.requestId, now);
		}
	}
};

/**
 * One vision action's failure, from catch to audit to a page-safe answer.
 *
 * A refusal is audited too, the same reasoning `/` dashboard's own actions
 * use: an attempt the operator made is an attempt on the record, whether
 * cs71-vision accepted it or not.
 */
function failVisionAction(
	control: string,
	user: UserRecord,
	error: unknown,
	requestId: string,
	now: Date
) {
	const { database } = webRuntime();
	const invalid = error instanceof InvalidCommandError;
	const visionError = error instanceof VisionError ? error : undefined;
	recordAudit(
		database,
		{
			userId: user.userId,
			role: user.role,
			action: `vision.${control}`,
			outcome: invalid || visionError?.kind === 'rejected' ? 'refused' : 'failed',
			requestId
		},
		now
	);
	reportToServerLog(error, requestId);

	if (invalid) {
		return fail(400, { control, error: INVALID_MESSAGE });
	}
	const { status, message } = safeResponseFor(error);
	return fail(status, { control, error: message });
}

function integerField(form: FormData, name: string): number {
	const value = form.get(name);
	if (typeof value !== 'string' || !/^\d{1,15}$/.test(value)) {
		throw new InvalidCommandError(`the ${name} field must be a non-negative integer`);
	}
	return Number(value);
}

function booleanField(form: FormData, name: string): boolean {
	const value = form.get(name);
	if (value !== 'true' && value !== 'false') {
		throw new InvalidCommandError(`the ${name} field must be true or false`);
	}
	return value === 'true';
}

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

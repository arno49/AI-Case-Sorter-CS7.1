/**
 * The only way this workspace speaks to `cs71-vision`'s api.
 *
 * A handful of resources - dataset review (PI-VISION-003), and
 * training/versioning/activation (PI-VISION-004/005) - so this client stays
 * a fraction of `daemon/client.ts`'s size deliberately. It reuses the same
 * Unix-socket transport (`daemon/transport.ts#exchange`, which is generic
 * infrastructure despite living in the daemon module) and the same
 * credential reader (`daemon/credentials.ts#readServiceToken`) rather than
 * forking either: `cs71-vision` authenticates callers with the same shared
 * service credential `cs71d` does, so `cs71-web`'s own copy of it (already
 * read for the daemon) works here unchanged.
 *
 * Errors are re-wrapped into this module's own `VisionError` rather than
 * left as `DaemonError`: nothing named "Daemon" should leak into a caller
 * that is not talking to `cs71d`.
 *
 * No idempotency key, generation or actor travels with a train/activate/
 * rollback call: `cs71-vision` has no concept of "which operator", and
 * never will for this surface - attribution is `cs71-web`'s job
 * (`recordAudit`, called by the route action that calls this client), the
 * same way `cs71d` never learns a browser identity either.
 */

import { InvalidCommandError } from '../daemon/client';
import { readServiceToken } from '../daemon/credentials';
import { DaemonError } from '../daemon/errors';
import { exchange, type DaemonReply } from '../daemon/transport';
import { VisionError, type VisionFailureKind } from './errors';

export { InvalidCommandError };

export const API_VERSION = 'v1';

export const DEADLINES = Object.freeze({ read: 5_000, write: 5_000 });

export interface DatasetClassSummary {
	readonly slot: number;
	readonly count: number;
	readonly eligible: boolean;
}

export interface DatasetSummary {
	readonly minimumExamplesPerClass: number;
	readonly classes: readonly DatasetClassSummary[];
	readonly trainingReady: boolean;
}

export interface CandidateSummary {
	readonly version: number;
	readonly trainedAt: string;
	readonly includedClasses: readonly number[];
	readonly excludedClasses: readonly number[];
	readonly accuracyByClass: Readonly<Record<number, number>>;
	readonly minimumExamplesPerClass: number;
	readonly trainingExampleCount: number;
	readonly holdoutExampleCount: number;
}

export interface ModelsSummary {
	readonly activeVersion: number | null;
	/** Whether a previous activation exists to roll back to right now. */
	readonly canRollBack: boolean;
	readonly candidates: readonly CandidateSummary[];
}

export interface TrainResult {
	/** False means a run was already in flight; nothing new was started. */
	readonly started: boolean;
}

export interface ActivationResult {
	readonly activeVersion: number;
	readonly activatedAt: string;
}

export interface Suggestion {
	readonly slot: number;
	readonly confidence: number;
	readonly modelVersion: number;
	readonly suggestedAt: string;
	/**
	 * The primer-presence axis (PI-VISION-010, ADR-0013/SAF-09), permanently
	 * separate from slot/confidence. Null means unknown/ambiguous, not "no
	 * primer" - see `requiresConfirmation`, which is the single source of
	 * truth for what this means; nothing in this workspace re-derives it.
	 */
	readonly primerPresent: boolean | null;
	/** Whether a person must explicitly confirm this case before it may be acted on. */
	readonly requiresConfirmation: boolean;
}

export interface SuggestionResult {
	/** Null when nothing has been classified yet - no active model, or no frame captured. */
	readonly suggestion: Suggestion | null;
}

export interface SuggestionAccuracy {
	readonly total: number;
	readonly correct: number;
	/** Null when nothing has been matched to an operator's choice yet. */
	readonly accuracy: number | null;
}

/**
 * One class's reviewed autonomous-sort record (PI-VISION-008, ADR-0013).
 *
 * `total`/`correct` count only reviewed attempts - a class with attempts
 * but no review yet has no entry here at all, never a fabricated 0% false
 * rate. "Unreviewed" and "reviewed clean" must never look the same.
 */
export interface AutonomousAccuracy {
	readonly total: number;
	readonly correct: number;
	readonly falseRate: number | null;
}

/** One autonomous sort submitted to `cs71d`, awaiting a human's verdict. */
export interface PendingAutonomousReview {
	readonly attemptId: number;
	readonly suggestionId: number;
	readonly operationId: string;
	readonly slot: number;
	readonly attemptedAt: string;
}

export interface AutonomySummary {
	/** Per-class confidence threshold currently configured, keyed by slot. */
	readonly thresholds: Readonly<Record<number, number>>;
	readonly accuracyByClass: Readonly<Record<number, AutonomousAccuracy>>;
	readonly pendingReview: readonly PendingAutonomousReview[];
}

export interface AutonomousReviewResult {
	readonly attemptId: number;
	readonly correct: boolean;
	readonly reviewedAt: string;
}

export interface VisionClientOptions {
	readonly socketPath: string;
	readonly serviceTokenPath: string;
}

export class VisionClient {
	#token: string | undefined;

	constructor(private readonly options: VisionClientOptions) {}

	async datasetSummary(): Promise<DatasetSummary> {
		const reply = await this.#get('/v1/dataset');
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseDatasetSummary(reply.body, reply.status);
	}

	async models(): Promise<ModelsSummary> {
		const reply = await this.#get('/v1/models');
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseModelsSummary(reply.body, reply.status);
	}

	/** Start a training run in the background. Never blocks on it finishing. */
	async train(): Promise<TrainResult> {
		const reply = await this.#post('/v1/train');
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseTrainResult(reply.body, reply.status);
	}

	async activate(version: number): Promise<ActivationResult> {
		if (!Number.isInteger(version) || version < 1) {
			throw new InvalidCommandError('a model version must be a positive integer');
		}
		const reply = await this.#post(`/v1/models/${version}/activate`);
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseActivationResult(reply.body, reply.status);
	}

	/** Activate whatever version was active immediately before the current one. */
	async rollback(): Promise<ActivationResult> {
		const reply = await this.#post('/v1/rollback');
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseActivationResult(reply.body, reply.status);
	}

	/**
	 * The most recently classified suggestion, if any.
	 *
	 * A pure read - this never classifies on demand and never issues a
	 * `cs71d` command, under any configuration. `cs71-vision`'s own
	 * background loop is the only thing that ever produces one.
	 */
	async suggestion(): Promise<SuggestionResult> {
		const reply = await this.#get('/v1/suggestion');
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseSuggestionResult(reply.body, reply.status);
	}

	/** Live suggestion accuracy - separate evidence from training-time held-out accuracy. */
	async suggestionAccuracy(): Promise<SuggestionAccuracy> {
		const reply = await this.#get('/v1/suggestion-accuracy');
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseSuggestionAccuracy(reply.body, reply.status);
	}

	/**
	 * Configured autonomy thresholds, reviewed per-class accuracy and the
	 * review queue (PI-VISION-008) - the evidence an operator looks at
	 * before ever lowering a class's threshold in configuration.
	 */
	async autonomy(): Promise<AutonomySummary> {
		const reply = await this.#get('/v1/autonomy');
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseAutonomySummary(reply.body, reply.status);
	}

	/**
	 * Record a human's verdict on one autonomous attempt.
	 *
	 * The only source of ground truth for an autonomous sort: there is no
	 * operator confirmation step to compare against the way a manual sort's
	 * suggestion accuracy already is.
	 */
	async reviewAutonomousAttempt(
		attemptId: number,
		correct: boolean
	): Promise<AutonomousReviewResult> {
		if (!Number.isInteger(attemptId) || attemptId < 1) {
			throw new InvalidCommandError('an attempt id must be a positive integer');
		}
		const reply = await this.#post(
			'/v1/autonomous-reviews',
			JSON.stringify({ api_version: API_VERSION, attempt_id: attemptId, correct })
		);
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return parseAutonomousReviewResult(reply.body, reply.status);
	}

	async #get(path: string): Promise<DaemonReply> {
		return this.#send('GET', path, DEADLINES.read);
	}

	async #post(path: string, body?: string): Promise<DaemonReply> {
		return this.#send('POST', path, DEADLINES.write, body);
	}

	async #send(
		method: 'GET' | 'POST',
		path: string,
		deadlineMs: number,
		body?: string
	): Promise<DaemonReply> {
		try {
			return await exchange(this.options.socketPath, {
				method,
				path,
				headers: {
					authorization: `Bearer ${this.#serviceToken()}`,
					accept: 'application/json',
					...(body === undefined ? {} : { 'content-type': 'application/json' })
				},
				body,
				timeoutMs: deadlineMs + 2_000
			});
		} catch (cause) {
			throw fromTransportFailure(cause);
		}
	}

	#serviceToken(): string {
		// Read once, the same reasoning `daemon/client.ts` uses: the file is
		// owner-only, and a rotation is a service restart, not a re-read.
		this.#token ??= readServiceToken(this.options.serviceTokenPath);
		return this.#token;
	}
}

/** A network-level failure from `exchange()`, which always throws `DaemonError`. */
function fromTransportFailure(cause: unknown): VisionError {
	if (cause instanceof DaemonError) {
		return new VisionError({
			kind: cause.kind as VisionFailureKind,
			status: cause.status,
			detail: cause.detail ?? cause.message,
			cause
		});
	}
	return new VisionError({ kind: 'unreachable', detail: String(cause), cause });
}

/** Turn a refusal into an error that carries cs71-vision's own correlation. */
function rejection(status: number, body: unknown): VisionError {
	const error = asRecord(body);
	const code = error?.code;
	const message = error?.message;
	return new VisionError({
		kind: 'rejected',
		status,
		code: typeof code === 'string' ? code : undefined,
		detail: typeof message === 'string' ? message : undefined
	});
}

function parseDatasetSummary(body: unknown, status: number): DatasetSummary {
	const record = asRecord(body);
	if (record?.api_version !== API_VERSION || !Array.isArray(record.classes)) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without the expected dataset shape'
		});
	}
	const minimum = record.minimum_examples_per_class;
	const trainingReady = record.training_ready;
	if (typeof minimum !== 'number' || typeof trainingReady !== 'boolean') {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without a usable floor or readiness flag'
		});
	}
	return {
		minimumExamplesPerClass: minimum,
		classes: record.classes.map((item) => parseClassSummary(item, status)),
		trainingReady
	};
}

function parseClassSummary(item: unknown, status: number): DatasetClassSummary {
	const record = asRecord(item);
	const slot = record?.slot;
	const count = record?.count;
	const eligible = record?.eligible;
	if (typeof slot !== 'number' || typeof count !== 'number' || typeof eligible !== 'boolean') {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered with an unusable class entry'
		});
	}
	return { slot, count, eligible };
}

function parseModelsSummary(body: unknown, status: number): ModelsSummary {
	const record = asRecord(body);
	if (record?.api_version !== API_VERSION || !Array.isArray(record.candidates)) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without the expected models shape'
		});
	}
	const activeVersion = record.active_version;
	const canRollBack = record.can_roll_back;
	if (
		(activeVersion !== null && typeof activeVersion !== 'number') ||
		typeof canRollBack !== 'boolean'
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered with an unusable active_version or can_roll_back'
		});
	}
	return {
		activeVersion,
		canRollBack,
		candidates: record.candidates.map((item) => parseCandidateSummary(item, status))
	};
}

function parseCandidateSummary(item: unknown, status: number): CandidateSummary {
	const record = asRecord(item);
	const version = record?.version;
	const trainedAt = record?.trained_at;
	const includedClasses = record?.included_classes;
	const excludedClasses = record?.excluded_classes;
	const accuracyByClass = record?.accuracy_by_class;
	const minimum = record?.minimum_examples_per_class;
	const trainingCount = record?.training_example_count;
	const holdoutCount = record?.holdout_example_count;
	if (
		typeof version !== 'number' ||
		typeof trainedAt !== 'string' ||
		!Array.isArray(includedClasses) ||
		!Array.isArray(excludedClasses) ||
		typeof accuracyByClass !== 'object' ||
		accuracyByClass === null ||
		typeof minimum !== 'number' ||
		typeof trainingCount !== 'number' ||
		typeof holdoutCount !== 'number'
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered with an unusable candidate entry'
		});
	}
	return {
		version,
		trainedAt,
		includedClasses: includedClasses.filter((value): value is number => typeof value === 'number'),
		excludedClasses: excludedClasses.filter((value): value is number => typeof value === 'number'),
		accuracyByClass: numericAccuracyMap(accuracyByClass as Record<string, unknown>),
		minimumExamplesPerClass: minimum,
		trainingExampleCount: trainingCount,
		holdoutExampleCount: holdoutCount
	};
}

function numericAccuracyMap(raw: Record<string, unknown>): Readonly<Record<number, number>> {
	const entries: [number, number][] = [];
	for (const [slot, accuracy] of Object.entries(raw)) {
		if (typeof accuracy === 'number') {
			entries.push([Number(slot), accuracy]);
		}
	}
	return Object.fromEntries(entries);
}

function parseTrainResult(body: unknown, status: number): TrainResult {
	const record = asRecord(body);
	const started = record?.started;
	if (record?.api_version !== API_VERSION || typeof started !== 'boolean') {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without a usable started flag'
		});
	}
	return { started };
}

function parseActivationResult(body: unknown, status: number): ActivationResult {
	const record = asRecord(body);
	const activeVersion = record?.active_version;
	const activatedAt = record?.activated_at;
	if (
		record?.api_version !== API_VERSION ||
		typeof activeVersion !== 'number' ||
		typeof activatedAt !== 'string'
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without a usable activation result'
		});
	}
	return { activeVersion, activatedAt };
}

function parseSuggestionResult(body: unknown, status: number): SuggestionResult {
	const record = asRecord(body);
	if (record?.api_version !== API_VERSION || !('suggestion' in record)) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without the expected suggestion shape'
		});
	}
	if (record.suggestion === null) {
		return { suggestion: null };
	}
	return { suggestion: parseSuggestion(record.suggestion, status) };
}

function parseSuggestion(item: unknown, status: number): Suggestion {
	const record = asRecord(item);
	const slot = record?.slot;
	const confidence = record?.confidence;
	const modelVersion = record?.model_version;
	const suggestedAt = record?.suggested_at;
	const primerPresent = record?.primer_present;
	const requiresConfirmation = record?.requires_confirmation;
	if (
		typeof slot !== 'number' ||
		typeof confidence !== 'number' ||
		typeof modelVersion !== 'number' ||
		typeof suggestedAt !== 'string' ||
		(primerPresent !== null && typeof primerPresent !== 'boolean') ||
		typeof requiresConfirmation !== 'boolean'
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered with an unusable suggestion'
		});
	}
	return {
		slot,
		confidence,
		modelVersion,
		suggestedAt,
		primerPresent,
		requiresConfirmation
	};
}

function parseSuggestionAccuracy(body: unknown, status: number): SuggestionAccuracy {
	const record = asRecord(body);
	const total = record?.total;
	const correct = record?.correct;
	const accuracy = record?.accuracy;
	if (
		record?.api_version !== API_VERSION ||
		typeof total !== 'number' ||
		typeof correct !== 'number' ||
		(accuracy !== null && typeof accuracy !== 'number')
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without a usable suggestion accuracy'
		});
	}
	return { total, correct, accuracy };
}

function parseAutonomySummary(body: unknown, status: number): AutonomySummary {
	const record = asRecord(body);
	const thresholds = record?.thresholds;
	const accuracyByClass = record?.accuracy_by_class;
	const pendingReview = record?.pending_review;
	if (
		record?.api_version !== API_VERSION ||
		typeof thresholds !== 'object' ||
		thresholds === null ||
		typeof accuracyByClass !== 'object' ||
		accuracyByClass === null ||
		!Array.isArray(pendingReview)
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without the expected autonomy shape'
		});
	}
	return {
		thresholds: numericAccuracyMap(thresholds as Record<string, unknown>),
		accuracyByClass: autonomousAccuracyMap(accuracyByClass as Record<string, unknown>, status),
		pendingReview: pendingReview.map((item) => parsePendingAutonomousReview(item, status))
	};
}

function autonomousAccuracyMap(
	raw: Record<string, unknown>,
	status: number
): Readonly<Record<number, AutonomousAccuracy>> {
	const entries: [number, AutonomousAccuracy][] = [];
	for (const [slot, item] of Object.entries(raw)) {
		const record = asRecord(item);
		const total = record?.total;
		const correct = record?.correct;
		const falseRate = record?.false_rate;
		if (
			typeof total !== 'number' ||
			typeof correct !== 'number' ||
			(falseRate !== null && typeof falseRate !== 'number')
		) {
			throw new VisionError({
				kind: 'malformed',
				status,
				detail: 'cs71-vision answered with an unusable autonomous accuracy entry'
			});
		}
		entries.push([Number(slot), { total, correct, falseRate }]);
	}
	return Object.fromEntries(entries);
}

function parsePendingAutonomousReview(item: unknown, status: number): PendingAutonomousReview {
	const record = asRecord(item);
	const attemptId = record?.attempt_id;
	const suggestionId = record?.suggestion_id;
	const operationId = record?.operation_id;
	const slot = record?.slot;
	const attemptedAt = record?.attempted_at;
	if (
		typeof attemptId !== 'number' ||
		typeof suggestionId !== 'number' ||
		typeof operationId !== 'string' ||
		typeof slot !== 'number' ||
		typeof attemptedAt !== 'string'
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered with an unusable pending review entry'
		});
	}
	return { attemptId, suggestionId, operationId, slot, attemptedAt };
}

function parseAutonomousReviewResult(body: unknown, status: number): AutonomousReviewResult {
	const record = asRecord(body);
	const attemptId = record?.attempt_id;
	const correct = record?.correct;
	const reviewedAt = record?.reviewed_at;
	if (
		record?.api_version !== API_VERSION ||
		typeof attemptId !== 'number' ||
		typeof correct !== 'boolean' ||
		typeof reviewedAt !== 'string'
	) {
		throw new VisionError({
			kind: 'malformed',
			status,
			detail: 'cs71-vision answered without a usable autonomous review result'
		});
	}
	return { attemptId, correct, reviewedAt };
}

function asRecord(body: unknown): Record<string, unknown> | undefined {
	return typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : undefined;
}

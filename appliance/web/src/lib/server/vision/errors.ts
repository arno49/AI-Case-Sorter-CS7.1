/**
 * What went wrong talking to `cs71-vision`, and what the operator is told.
 *
 * `cs71-vision`'s api has no generated contract of its own (see
 * `appliance/vision/src/cs71vision/api.py`'s docstring for why that scope
 * call was made), so there is no generated `ErrorCode` type to key this
 * mapping on the way `daemon/errors.ts` keys its own - the codes below are
 * transcribed by hand from `cs71vision.api`'s own small, stable vocabulary.
 *
 * PI-VISION-003's dataset read had no per-code distinction worth making -
 * every failure meant the same thing to an operator, "the numbers could not
 * be shown right now". PI-VISION-005 changed that: training, activating and
 * rolling back are real actions with real, distinct refusal reasons an
 * operator needs to understand (a version that is not on record, a rollback
 * with nothing to roll back to), so this module grew a per-code mapping the
 * same shape `daemon/errors.ts` already uses, once there was something
 * meaningful to say per code.
 */

export type VisionFailureKind =
	/** The socket was not there, or refused the connection. */
	| 'unreachable'
	/** No answer inside the deadline this service allowed. */
	| 'timeout'
	/** An answer that is not the shape this client expects. */
	| 'malformed'
	/** A well-formed refusal from cs71-vision. */
	| 'rejected';

export interface SafeVisionResponse {
	readonly status: number;
	readonly message: string;
}

const UNAVAILABLE_MESSAGE = 'The classifier service is not answering.';

/**
 * Every code `cs71vision.api` can return, mapped to something an operator
 * can act on. `VALIDATION_FAILED` today means exactly one thing - a
 * rollback with no previous version recorded (`_rollback_body` in
 * `cs71vision/api.py`) - and this wording is specific to that; if a second
 * `VALIDATION_FAILED` case is ever added there, this mapping needs to grow
 * with it rather than staying accurate by accident.
 */
const SAFE_RESPONSES: Readonly<Record<string, SafeVisionResponse>> = Object.freeze({
	VALIDATION_FAILED: { status: 400, message: 'There is no previous version to roll back to.' },
	// The browser never authenticates to cs71-vision: this means this
	// service's own credential is wrong, which is not the operator's
	// problem and must not be described to them as a permission error.
	UNAUTHENTICATED: { status: 502, message: UNAVAILABLE_MESSAGE },
	RESOURCE_NOT_FOUND: { status: 404, message: 'That model version is not on record.' },
	INTERNAL_ERROR: { status: 502, message: UNAVAILABLE_MESSAGE }
});

const KIND_RESPONSES: Readonly<Record<Exclude<VisionFailureKind, 'rejected'>, SafeVisionResponse>> =
	Object.freeze({
		unreachable: { status: 503, message: UNAVAILABLE_MESSAGE },
		timeout: { status: 504, message: UNAVAILABLE_MESSAGE },
		malformed: { status: 502, message: UNAVAILABLE_MESSAGE }
	});

export interface VisionErrorDetails {
	readonly kind: VisionFailureKind;
	readonly status?: number;
	readonly code?: string;
	/** cs71-vision's own words. For the server log, never for a page. */
	readonly detail?: string;
	readonly cause?: unknown;
}

export class VisionError extends Error {
	readonly kind: VisionFailureKind;
	readonly status: number | undefined;
	readonly code: string | undefined;
	readonly detail: string | undefined;

	constructor(details: VisionErrorDetails) {
		super(describe(details), details.cause === undefined ? undefined : { cause: details.cause });
		this.name = 'VisionError';
		this.kind = details.kind;
		this.status = details.status;
		this.code = details.code;
		this.detail = details.detail;
	}

	/** What may be shown to the browser. */
	get safeResponse(): SafeVisionResponse {
		if (this.kind !== 'rejected') {
			return KIND_RESPONSES[this.kind];
		}
		if (this.code === undefined) {
			return { status: 502, message: UNAVAILABLE_MESSAGE };
		}
		return SAFE_RESPONSES[this.code] ?? { status: 502, message: UNAVAILABLE_MESSAGE };
	}
}

export function safeResponseFor(error: unknown): SafeVisionResponse {
	return error instanceof VisionError
		? error.safeResponse
		: { status: 503, message: UNAVAILABLE_MESSAGE };
}

function describe(details: VisionErrorDetails): string {
	const parts = [`vision ${details.kind}`];
	if (details.status !== undefined) {
		parts.push(`http ${details.status}`);
	}
	if (details.code !== undefined) {
		parts.push(details.code);
	}
	if (details.detail !== undefined) {
		parts.push(details.detail);
	}
	return parts.join(' ');
}

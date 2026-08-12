/**
 * What went wrong talking to `cs71-vision`, and what the operator is told.
 *
 * Deliberately smaller than `daemon/errors.ts`: `cs71-vision`'s dataset api
 * (PI-VISION-003) is one read-only resource with no generated contract of its
 * own (see `appliance/vision/src/cs71vision/api.py`'s docstring for why that
 * scope call was made). There is no per-code recovery behavior to express
 * the way the daemon's commands need - every failure here means the same
 * thing to an operator: the dataset review numbers could not be shown right
 * now. This module still keeps the raw detail server-side only, the same
 * discipline `daemon/errors.ts` applies.
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
		return { status: this.status === 404 ? 404 : 503, message: UNAVAILABLE_MESSAGE };
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

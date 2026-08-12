/**
 * What went wrong, and what the operator is told about it.
 *
 * Two audiences, deliberately separated. The server keeps the daemon's own
 * code, request id and message for correlation; the browser gets a sentence
 * this workspace wrote. Passing daemon text through would eventually leak
 * protocol vocabulary or a device path into a page, and it would tie the
 * wording an operator reads to a document that describes a machine interface.
 *
 * The mapping is deliberately conservative about safety. `UNCERTAIN` never
 * becomes a polite retry suggestion: the machine may have moved, and the person
 * reading the screen has to know that.
 */

import type { components } from '$lib/api/cs71d-v1';

export type DaemonErrorCode = components['schemas']['ErrorCode'];

export type DaemonFailureKind =
	/** The socket was not there, or refused the connection. */
	| 'unreachable'
	/** No answer inside the deadline this service allowed. */
	| 'timeout'
	/** An answer that is not the contract's. */
	| 'malformed'
	/** A well-formed refusal from the daemon. */
	| 'rejected';

export interface SafeResponse {
	readonly status: number;
	readonly message: string;
}

const UNCERTAIN_MESSAGE =
	'The machine state is UNCERTAIN: it may have moved. Follow the recovery' +
	' procedure before commanding it again.';

const UNAVAILABLE_MESSAGE = 'The machine service is not answering. The machine was not commanded.';

/**
 * Every code the contract can return, mapped to something an operator can act
 * on. A code with no entry falls back to the unavailable message rather than
 * being described by a name from the wire.
 */
const SAFE_RESPONSES: Readonly<Record<DaemonErrorCode, SafeResponse>> = Object.freeze({
	VALIDATION_FAILED: { status: 400, message: 'That request was rejected as invalid.' },
	// The browser never authenticates to the daemon: these two mean this
	// service's own credential is wrong, which is not the operator's problem
	// and must not be described to them as a permission error.
	UNAUTHENTICATED: { status: 502, message: UNAVAILABLE_MESSAGE },
	FORBIDDEN: { status: 502, message: UNAVAILABLE_MESSAGE },
	RESOURCE_NOT_FOUND: { status: 404, message: 'That operation is no longer on record.' },
	STALE_GENERATION: {
		status: 409,
		message: 'The machine changed since this page was loaded. Reload it and try again.'
	},
	IDEMPOTENCY_CONFLICT: {
		status: 409,
		message: 'That request was already submitted with different details.'
	},
	NOT_READY: { status: 409, message: 'The machine is not ready for that yet.' },
	UNCERTAIN: { status: 409, message: UNCERTAIN_MESSAGE },
	QUEUE_FULL: { status: 503, message: 'The machine is busy. Wait for the current work to finish.' },
	DEADLINE_INVALID: { status: 400, message: 'That request was rejected as invalid.' },
	DEADLINE_EXPIRED: {
		status: 504,
		message: 'The machine did not answer in time. Check its state before commanding it again.'
	},
	JOURNAL_UNAVAILABLE: {
		status: 503,
		message: 'The machine cannot record operations, so new motion is blocked. Service is required.'
	},
	SERVICE_UNAVAILABLE: { status: 503, message: UNAVAILABLE_MESSAGE },
	INTERNAL_ERROR: { status: 502, message: UNAVAILABLE_MESSAGE }
});

const KIND_RESPONSES: Readonly<Record<Exclude<DaemonFailureKind, 'rejected'>, SafeResponse>> =
	Object.freeze({
		unreachable: { status: 503, message: UNAVAILABLE_MESSAGE },
		// A request that timed out was still sent: the machine may be acting on it.
		timeout: {
			status: 504,
			message: 'The machine did not answer in time. Check its state before commanding it again.'
		},
		malformed: { status: 502, message: UNAVAILABLE_MESSAGE }
	});

export interface DaemonErrorDetails {
	readonly kind: DaemonFailureKind;
	readonly status?: number;
	readonly code?: DaemonErrorCode;
	/** The daemon's diagnostic id, for correlation in this service's audit. */
	readonly requestId?: string;
	/** The daemon's own words. For the server log, never for a page. */
	readonly detail?: string;
	readonly cause?: unknown;
}

export class DaemonError extends Error {
	readonly kind: DaemonFailureKind;
	readonly status: number | undefined;
	readonly code: DaemonErrorCode | undefined;
	readonly requestId: string | undefined;
	readonly detail: string | undefined;

	constructor(details: DaemonErrorDetails) {
		super(describe(details), details.cause === undefined ? undefined : { cause: details.cause });
		this.name = 'DaemonError';
		this.kind = details.kind;
		this.status = details.status;
		this.code = details.code;
		this.requestId = details.requestId;
		this.detail = details.detail;
	}

	/** What may be shown to the browser. */
	get safeResponse(): SafeResponse {
		if (this.kind !== 'rejected') {
			return KIND_RESPONSES[this.kind];
		}
		return this.code === undefined
			? { status: 502, message: UNAVAILABLE_MESSAGE }
			: SAFE_RESPONSES[this.code];
	}
}

export function safeResponseFor(error: unknown): SafeResponse {
	return error instanceof DaemonError
		? error.safeResponse
		: { status: 502, message: UNAVAILABLE_MESSAGE };
}

function describe(details: DaemonErrorDetails): string {
	const parts = [`daemon ${details.kind}`];
	if (details.status !== undefined) {
		parts.push(`http ${details.status}`);
	}
	if (details.code !== undefined) {
		parts.push(details.code);
	}
	if (details.requestId !== undefined) {
		parts.push(`request ${details.requestId}`);
	}
	if (details.detail !== undefined) {
		parts.push(details.detail);
	}
	return parts.join(' ');
}

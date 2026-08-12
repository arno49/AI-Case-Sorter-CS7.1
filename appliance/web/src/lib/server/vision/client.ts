/**
 * The only way this workspace speaks to `cs71-vision`'s dataset api.
 *
 * One read, one resource - `GET /v1/dataset` (PI-VISION-003), so this client
 * is a fraction of `daemon/client.ts`'s size deliberately. It reuses the same
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
 */

import { readServiceToken } from '../daemon/credentials';
import { DaemonError } from '../daemon/errors';
import { exchange, type DaemonReply } from '../daemon/transport';
import { VisionError, type VisionFailureKind } from './errors';

export const API_VERSION = 'v1';

export const DEADLINES = Object.freeze({ read: 5_000 });

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

	async #get(path: string): Promise<DaemonReply> {
		try {
			return await exchange(this.options.socketPath, {
				method: 'GET',
				path,
				headers: {
					authorization: `Bearer ${this.#serviceToken()}`,
					accept: 'application/json'
				},
				timeoutMs: DEADLINES.read + 2_000
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

function asRecord(body: unknown): Record<string, unknown> | undefined {
	return typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : undefined;
}

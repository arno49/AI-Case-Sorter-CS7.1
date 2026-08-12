/**
 * The only way this workspace speaks to `cs71d`.
 *
 * Every call is a named operation with typed arguments. There is no method that
 * takes a path, a device, or a protocol string, so a browser request cannot
 * become one: a form chooses a slot or a target, and this module decides what
 * that means on the wire. Types come from the generated contract, so a change
 * to `cs71d-v1.openapi.json` that this code has not caught up with fails the
 * build rather than a machine.
 *
 * The headers the contract requires are supplied here rather than by callers.
 * An idempotency key, a generation to match and a deadline are not decoration:
 * they are what stops a retried submission from moving the machine twice, a
 * stale page from acting on a machine that has changed, and a command from
 * outliving the operator's attention.
 *
 * An accepted command returns an `operation_id` and a pending state. It is
 * never a claim that the machine did anything.
 */

import { randomBytes } from 'node:crypto';

import type { components } from '$lib/api/cs71d-v1';
import type { Role } from '../auth/users';
import { readServiceToken } from './credentials';
import { DaemonError } from './errors';
import { exchange } from './transport';

type Schemas = components['schemas'];

export type Actor = Schemas['Actor'];
export type MachineSnapshot = Schemas['MachineSnapshot'];
export type Operation = Schemas['Operation'];
export type OperationAccepted = Schemas['OperationAccepted'];
export type OperationPage = Schemas['OperationPage'];
export type OperationState = Schemas['OperationState'];
export type OperationType = Schemas['OperationType'];
export type System = Schemas['System'];
export type Configuration = Schemas['Configuration'];
export type ConfigurationPatch = Schemas['ConfigurationPatch'];
export type HomeTarget = Schemas['HomeRequest']['target'];

export const API_VERSION = 'v1';

/**
 * How long each command may take, in milliseconds.
 *
 * A deadline is a promise to the operator about when they will know something,
 * so these follow the work rather than one convenient number: homing sweeps an
 * axis, a stop must not sit behind a long budget, and a read is either prompt
 * or not useful.
 */
export const DEADLINES = Object.freeze({
	read: 5_000,
	stop: 5_000,
	connect: 15_000,
	recover: 20_000,
	home: 60_000,
	sort: 15_000,
	feed: 15_000,
	configure: 10_000
});

const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._~-]{16,128}$/;
const OPERATION_ID_PATTERN =
	/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/;
const MAXIMUM_SLOT = 63;

/** Rejected before anything is sent: this is our own input, not the daemon's. */
export class InvalidCommandError extends Error {}

/** A fresh key, in the alphabet the contract allows. */
export function newIdempotencyKey(): string {
	return randomBytes(24).toString('base64url');
}

/** Attribution, not authentication: the daemon trusts the socket, not this. */
export function actorFor(user: { readonly userId: string; readonly role: Role }): Actor {
	return { user_id: user.userId, role: user.role };
}

export interface DaemonClientOptions {
	readonly socketPath: string;
	readonly serviceTokenPath: string;
}

/** A bounded, already-validated request for the durable operation history. */
export interface OperationHistoryQuery {
	readonly state?: OperationState;
	readonly type?: OperationType;
	readonly limit?: number;
	readonly cursor?: string;
}

/** What every state-changing command needs from its caller. */
export interface CommandContext {
	readonly actor: Actor;
	/** The generation the operator was looking at when they decided. */
	readonly generation: number;
	/** Supply one to make a resubmission the same command, not a second one. */
	readonly idempotencyKey?: string;
}

export class DaemonClient {
	#token: string | undefined;

	constructor(private readonly options: DaemonClientOptions) {}

	async snapshot(): Promise<MachineSnapshot> {
		return this.#read<MachineSnapshot>('/v1/snapshot');
	}

	async operation(operationId: string): Promise<Operation> {
		if (!OPERATION_ID_PATTERN.test(operationId)) {
			throw new InvalidCommandError('an operation id must be a UUID');
		}
		return this.#read<Operation>(`/v1/operations/${operationId}`);
	}

	/**
	 * A bounded page of the durable history, newest first.
	 *
	 * The query a caller passes here is already a `state`/`type` this contract
	 * knows and a `limit`/`cursor` within its bounds — validating a raw filter
	 * string is the caller's job, same as a slot or a home target, because the
	 * caller is the one holding the untrusted input.
	 */
	async operations(query: OperationHistoryQuery = {}): Promise<OperationPage> {
		const params = new URLSearchParams();
		if (query.state !== undefined) {
			params.set('state', query.state);
		}
		if (query.type !== undefined) {
			params.set('type', query.type);
		}
		if (query.limit !== undefined) {
			params.set('limit', String(query.limit));
		}
		if (query.cursor !== undefined) {
			params.set('cursor', query.cursor);
		}
		const search = params.toString();
		return this.#read<OperationPage>(`/v1/operations${search ? `?${search}` : ''}`);
	}

	async configuration(): Promise<Configuration> {
		return this.#read<Configuration>('/v1/configuration');
	}

	/**
	 * Static, session-independent facts — today, the DTR-gate status.
	 *
	 * `NOT_EXECUTED` is a documented project evidence-status label, not a
	 * protocol detail, and is never presented as a pass: see
	 * `$lib/system-view.ts` for the sentence that goes with it.
	 */
	async system(): Promise<System> {
		return this.#read<System>('/v1/system');
	}

	async connect(context: CommandContext): Promise<OperationAccepted> {
		return this.#command('/v1/session/connect', DEADLINES.connect, context, {
			api_version: API_VERSION,
			actor: context.actor
		});
	}

	/**
	 * Recovery, which the operator has to confirm explicitly.
	 *
	 * The confirmation is a required argument rather than a default, so that no
	 * caller can recover an `UNCERTAIN` machine by omission.
	 */
	async recover(
		context: CommandContext,
		confirmUncertainRecovery: true
	): Promise<OperationAccepted> {
		if (confirmUncertainRecovery !== true) {
			throw new InvalidCommandError('recovery requires explicit confirmation');
		}
		return this.#command('/v1/session/recover', DEADLINES.recover, context, {
			api_version: API_VERSION,
			actor: context.actor,
			confirm_uncertain_recovery: true
		});
	}

	/**
	 * Software stop.
	 *
	 * The generation defaults to `*`, which the contract allows here and nowhere
	 * else: a stop that was refused because the page was a few seconds old would
	 * be a stop that did not happen. This is not an emergency stop and must
	 * never be presented as one.
	 */
	async stop(
		context: Omit<CommandContext, 'generation'> & { readonly generation?: number | '*' }
	): Promise<OperationAccepted> {
		const generation = context.generation ?? '*';
		if (generation !== '*') {
			assertGeneration(generation);
		}
		return this.#send('/v1/machine/stop', {
			deadlineMs: DEADLINES.stop,
			generation: String(generation),
			idempotencyKey: context.idempotencyKey,
			body: { api_version: API_VERSION, actor: context.actor }
		});
	}

	async home(context: CommandContext, target: HomeTarget): Promise<OperationAccepted> {
		if (target !== 'feeder' && target !== 'sorter' && target !== 'both') {
			throw new InvalidCommandError(`unknown home target ${String(target)}`);
		}
		return this.#command('/v1/operations/home', DEADLINES.home, context, {
			api_version: API_VERSION,
			actor: context.actor,
			target
		});
	}

	async sort(context: CommandContext, slot: number): Promise<OperationAccepted> {
		if (!Number.isInteger(slot) || slot < 0 || slot > MAXIMUM_SLOT) {
			throw new InvalidCommandError(`a slot must be an integer from 0 to ${MAXIMUM_SLOT}`);
		}
		// The firmware capability snapshot may allow fewer slots than the
		// contract's ceiling; the daemon is the authority on that, not this range.
		return this.#command('/v1/operations/sort', DEADLINES.sort, context, {
			api_version: API_VERSION,
			actor: context.actor,
			slot
		});
	}

	async feed(context: CommandContext): Promise<OperationAccepted> {
		return this.#command('/v1/operations/feed', DEADLINES.feed, context, {
			api_version: API_VERSION,
			actor: context.actor
		});
	}

	async updateConfiguration(
		context: CommandContext,
		patch: ConfigurationPatch
	): Promise<OperationAccepted> {
		return this.#send('/v1/configuration', {
			method: 'PATCH',
			deadlineMs: DEADLINES.configure,
			generation: assertGeneration(context.generation),
			idempotencyKey: context.idempotencyKey,
			body: patch
		});
	}

	async #read<T>(path: string): Promise<T> {
		const reply = await exchange(this.options.socketPath, {
			method: 'GET',
			path,
			headers: this.#headers(DEADLINES.read),
			timeoutMs: this.#clientTimeout(DEADLINES.read)
		});
		if (reply.status !== 200) {
			throw rejection(reply.status, reply.body);
		}
		return expectVersioned<T>(reply.body, reply.status);
	}

	async #command(
		path: string,
		deadlineMs: number,
		context: CommandContext,
		body: unknown
	): Promise<OperationAccepted> {
		return this.#send(path, {
			deadlineMs,
			generation: assertGeneration(context.generation),
			idempotencyKey: context.idempotencyKey,
			body
		});
	}

	async #send(
		path: string,
		call: {
			readonly method?: 'POST' | 'PATCH';
			readonly deadlineMs: number;
			readonly generation: string;
			readonly idempotencyKey?: string;
			readonly body: unknown;
		}
	): Promise<OperationAccepted> {
		const reply = await exchange(this.options.socketPath, {
			method: call.method ?? 'POST',
			path,
			headers: {
				...this.#headers(call.deadlineMs),
				'idempotency-key': assertIdempotencyKey(call.idempotencyKey ?? newIdempotencyKey()),
				'if-match-generation': call.generation
			},
			body: JSON.stringify(call.body),
			timeoutMs: this.#clientTimeout(call.deadlineMs)
		});

		if (reply.status !== 202) {
			throw rejection(reply.status, reply.body);
		}
		return expectAccepted(reply.body, reply.status);
	}

	#headers(deadlineMs: number): Record<string, string> {
		return {
			authorization: `Bearer ${this.#serviceToken()}`,
			accept: 'application/json',
			'x-deadline-ms': String(deadlineMs)
		};
	}

	/**
	 * Wait a little past the deadline the daemon was given.
	 *
	 * Giving up first would turn the daemon's own timely refusal into an unknown
	 * outcome, which is the more expensive answer for an operator to act on.
	 */
	#clientTimeout(deadlineMs: number): number {
		return deadlineMs + 2_000;
	}

	#serviceToken(): string {
		// Read once: the file is owner-only, and re-reading it on every command
		// would put a credential through the filesystem far more often than
		// rotation needs. A rotation is a service restart.
		this.#token ??= readServiceToken(this.options.serviceTokenPath);
		return this.#token;
	}
}

function assertGeneration(generation: number): string {
	if (!Number.isInteger(generation) || generation < 0) {
		throw new InvalidCommandError('a generation must be a non-negative integer');
	}
	return String(generation);
}

function assertIdempotencyKey(key: string): string {
	if (!IDEMPOTENCY_KEY_PATTERN.test(key)) {
		throw new InvalidCommandError('an idempotency key must be 16-128 unreserved characters');
	}
	return key;
}

/** Turn a refusal into an error that carries the daemon's own correlation. */
function rejection(status: number, body: unknown): DaemonError {
	const error = asRecord(body);
	const code = error?.code;
	const requestId = error?.request_id;
	const message = error?.message;
	return new DaemonError({
		kind: 'rejected',
		status,
		code: typeof code === 'string' ? (code as Schemas['ErrorCode']) : undefined,
		requestId: typeof requestId === 'string' ? requestId : undefined,
		detail: typeof message === 'string' ? message : undefined
	});
}

function expectVersioned<T>(body: unknown, status: number): T {
	const record = asRecord(body);
	if (record?.api_version !== API_VERSION) {
		throw new DaemonError({
			kind: 'malformed',
			status,
			detail: 'the daemon answered without the expected api_version'
		});
	}
	return record as T;
}

function expectAccepted(body: unknown, status: number): OperationAccepted {
	const accepted = expectVersioned<OperationAccepted>(body, status);
	if (
		typeof accepted.operation_id !== 'string' ||
		!OPERATION_ID_PATTERN.test(accepted.operation_id) ||
		typeof accepted.state !== 'string' ||
		typeof accepted.generation !== 'number'
	) {
		throw new DaemonError({
			kind: 'malformed',
			status,
			detail: 'the daemon accepted a command without a usable operation identity'
		});
	}
	return accepted;
}

function asRecord(body: unknown): Record<string, unknown> | undefined {
	return typeof body === 'object' && body !== null ? (body as Record<string, unknown>) : undefined;
}

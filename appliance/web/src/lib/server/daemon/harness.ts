/**
 * A stand-in `cs71d` on a real Unix domain socket.
 *
 * The point of listening on an actual socket rather than stubbing the transport
 * is that the specs then cover what production does: a connection to a path, a
 * real HTTP exchange, real headers, and a real credential read from a file with
 * real permissions.
 */

import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { chmodSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const SERVICE_TOKEN = 'a-local-service-credential';

export interface RecordedRequest {
	readonly method: string;
	readonly path: string;
	readonly headers: Readonly<Record<string, string>>;
	readonly body: string;
}

export type DaemonHandler = (request: RecordedRequest, response: ServerResponse) => void;

export interface FakeDaemon {
	readonly socketPath: string;
	readonly serviceTokenPath: string;
	readonly directory: string;
	readonly requests: RecordedRequest[];
	/** The last request, which is what a single-call spec means by "the request". */
	readonly lastRequest: () => RecordedRequest;
	answerWith: (handler: DaemonHandler) => void;
	close: () => Promise<void>;
}

/** Answer any request with a JSON body and a status. */
export function replying(status: number, body: unknown): DaemonHandler {
	return (_request, response) => {
		response.writeHead(status, { 'content-type': 'application/json' });
		response.end(JSON.stringify(body));
	};
}

export async function startFakeDaemon(): Promise<FakeDaemon> {
	// Kept short: a Unix socket path has a hard length limit far below PATH_MAX.
	const directory = mkdtempSync(join(tmpdir(), 'cs71d-'));
	const socketPath = join(directory, 's');
	const serviceTokenPath = join(directory, 'token');
	writeFileSync(serviceTokenPath, `${SERVICE_TOKEN}\n`);
	chmodSync(serviceTokenPath, 0o600);

	const requests: RecordedRequest[] = [];
	let handler: DaemonHandler = replying(500, { message: 'no answer was prepared' });

	const server: Server = createServer((incoming: IncomingMessage, response: ServerResponse) => {
		const chunks: Buffer[] = [];
		incoming.on('data', (chunk: Buffer) => chunks.push(chunk));
		incoming.on('end', () => {
			const record: RecordedRequest = {
				method: incoming.method ?? '',
				path: incoming.url ?? '',
				headers: incoming.headers as Record<string, string>,
				body: Buffer.concat(chunks).toString('utf8')
			};
			requests.push(record);
			handler(record, response);
		});
	});

	await new Promise<void>((resolve) => server.listen(socketPath, resolve));

	return {
		socketPath,
		serviceTokenPath,
		directory,
		requests,
		lastRequest: () => requests[requests.length - 1],
		answerWith: (next: DaemonHandler) => {
			handler = next;
		},
		close: async () => {
			await new Promise<void>((resolve) => server.close(() => resolve()));
			rmSync(directory, { recursive: true, force: true });
		}
	};
}

export interface FakeEventStream {
	readonly handler: DaemonHandler;
	/** The `Last-Event-ID` header each connection arrived with, in order. */
	readonly cursors: (string | undefined)[];
	/**
	 * Resolves when the *next* reader attaches.
	 *
	 * Call it before starting the reader: a frame written while nothing is
	 * attached goes nowhere, and a spec that raced that would hang rather than
	 * fail.
	 */
	nextConnection: () => Promise<void>;
	/** Push one event to every attached reader. */
	send: (event: Record<string, unknown>) => void;
	/** Push a raw frame, for the shapes a well-behaved daemon would not send. */
	raw: (frame: string) => void;
	/** End the response, as a daemon that is shutting down would. */
	end: () => void;
}

/** A daemon that holds the connection open and streams events on demand. */
export function eventStream(): FakeEventStream {
	const open: ServerResponse[] = [];
	const cursors: (string | undefined)[] = [];
	const waiting: (() => void)[] = [];

	return {
		handler: (request, response) => {
			cursors.push(request.headers['last-event-id']);
			response.writeHead(200, {
				'content-type': 'text/event-stream',
				'cache-control': 'no-store'
			});
			open.push(response);
			for (const resolve of waiting.splice(0)) {
				resolve();
			}
		},
		cursors,
		nextConnection: () => new Promise<void>((resolve) => waiting.push(resolve)),
		send(event) {
			this.raw(`data: ${JSON.stringify(event)}\n\n`);
		},
		raw: (frame) => {
			for (const response of open) {
				response.write(frame);
			}
		},
		end: () => {
			for (const response of open.splice(0)) {
				response.end();
			}
		}
	};
}

/** A minimal daemon event, for specs that care about the stream rather than it. */
export function daemonEvent(overrides: Record<string, unknown> = {}): Record<string, unknown> {
	return {
		api_version: 'v1',
		event_id: 1,
		occurred_at: '2026-08-11T12:00:00.000Z',
		generation: 7,
		type: 'snapshot.changed',
		data: {},
		...overrides
	};
}

/** A minimal accepted-operation body, for specs that care about the headers. */
export function acceptedOperation(
	overrides: Record<string, unknown> = {}
): Record<string, unknown> {
	return {
		api_version: 'v1',
		operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
		state: 'ACCEPTED',
		generation: 7,
		accepted_at: '2026-08-11T12:00:00.000Z',
		status_url: '/v1/operations/0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
		...overrides
	};
}

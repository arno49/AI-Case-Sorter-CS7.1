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

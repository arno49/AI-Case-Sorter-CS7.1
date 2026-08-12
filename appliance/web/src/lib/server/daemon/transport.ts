/**
 * One HTTP exchange with `cs71d`, over its Unix domain socket.
 *
 * There is no host, no port and no URL anywhere in this module: the socket path
 * comes from configuration, and the request path is a literal built by
 * `client.ts`. A browser therefore has nothing to point somewhere else, which
 * is the property the architecture calls socket-only.
 *
 * The response is treated as untrusted input even though it comes from a
 * process on the same machine: it is size-capped while it arrives, and parsed
 * rather than assumed. A daemon that has gone wrong should make a request fail,
 * not make this process run out of memory.
 */

import { request as httpRequest } from 'node:http';

import { DaemonError } from './errors';

/** Generous for a snapshot or an operation page, far below a memory problem. */
export const MAXIMUM_RESPONSE_BYTES = 256 * 1024;

/** Only the contract's own namespace is reachable through this transport. */
const ALLOWED_PATH = /^\/v1\/[A-Za-z0-9\-._~/]*(\?[A-Za-z0-9\-._~/=&%]*)?$/;

export interface DaemonExchange {
	readonly method: 'GET' | 'POST' | 'PATCH';
	readonly path: string;
	readonly headers: Readonly<Record<string, string>>;
	/** Serialized JSON, or nothing for a read. */
	readonly body?: string;
	readonly timeoutMs: number;
}

export interface DaemonReply {
	readonly status: number;
	/** Parsed JSON, or `undefined` when the daemon sent no body. */
	readonly body: unknown;
}

export async function exchange(socketPath: string, call: DaemonExchange): Promise<DaemonReply> {
	if (!ALLOWED_PATH.test(call.path) || call.path.includes('..')) {
		// Nothing user-supplied should be able to reach this, so reaching it is a
		// programming error rather than a request to be answered. It rejects
		// rather than throwing synchronously, so one call site handles both.
		throw new DaemonError({ kind: 'malformed', detail: `refusing daemon path ${call.path}` });
	}

	return new Promise<DaemonReply>((resolve, reject) => {
		const outgoing = httpRequest(
			{
				socketPath,
				path: call.path,
				method: call.method,
				headers: {
					...call.headers,
					...(call.body === undefined
						? {}
						: {
								'content-type': 'application/json',
								'content-length': String(Buffer.byteLength(call.body))
							})
				}
			},
			(incoming) => {
				const chunks: Buffer[] = [];
				let received = 0;

				incoming.on('data', (chunk: Buffer) => {
					received += chunk.length;
					if (received > MAXIMUM_RESPONSE_BYTES) {
						incoming.destroy();
						reject(
							new DaemonError({
								kind: 'malformed',
								status: incoming.statusCode,
								detail: `the daemon sent more than ${MAXIMUM_RESPONSE_BYTES} bytes`
							})
						);
						return;
					}
					chunks.push(chunk);
				});

				incoming.on('end', () => {
					const status = incoming.statusCode ?? 0;
					const text = Buffer.concat(chunks).toString('utf8');
					if (text.trim() === '') {
						resolve({ status, body: undefined });
						return;
					}
					try {
						resolve({ status, body: JSON.parse(text) });
					} catch (cause) {
						reject(
							new DaemonError({
								kind: 'malformed',
								status,
								detail: 'the daemon sent a body that is not JSON',
								cause
							})
						);
					}
				});

				incoming.on('error', (cause) => {
					reject(new DaemonError({ kind: 'unreachable', detail: 'the reply was cut off', cause }));
				});
			}
		);

		outgoing.setTimeout(call.timeoutMs, () => {
			// The request was sent; the machine may already be acting on it. The
			// caller has to treat this as unknown, not as "did not happen".
			outgoing.destroy(
				new DaemonError({
					kind: 'timeout',
					detail: `no answer within ${call.timeoutMs} ms`
				})
			);
		});

		outgoing.on('error', (cause) => {
			reject(
				cause instanceof DaemonError
					? cause
					: new DaemonError({
							kind: 'unreachable',
							detail: `cannot reach the daemon at ${socketPath}`,
							cause
						})
			);
		});

		if (call.body !== undefined) {
			outgoing.write(call.body);
		}
		outgoing.end();
	});
}

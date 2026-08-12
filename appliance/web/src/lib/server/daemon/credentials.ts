/**
 * The credential this service presents to `cs71d`.
 *
 * It is read from a protected file, never from configuration or the
 * environment: both are readable by any local user through the process table,
 * and a credential that authenticates a machine controller is not something to
 * leave where `ps` can find it. The daemon reads the same file under the same
 * rules; this is the web side of that agreement.
 *
 * A file other users can read is refused rather than repaired. Tightening the
 * mode ourselves would hide that it may already have been copied.
 */

import { readFileSync, statSync } from 'node:fs';

/** Any access bit for group or other. The daemon refuses the same. */
const OTHER_ACCESS_MASK = 0o077;

export class ServiceCredentialError extends Error {}

export function readServiceToken(path: string): string {
	let mode: number;
	let contents: string;
	try {
		mode = statSync(path).mode & 0o777;
		contents = readFileSync(path, 'utf8');
	} catch (cause) {
		throw new ServiceCredentialError(
			`cannot read the daemon service token at ${path}: ${describe(cause)}`,
			{ cause }
		);
	}

	if ((mode & OTHER_ACCESS_MASK) !== 0) {
		throw new ServiceCredentialError(
			`the daemon service token at ${path} is reachable by other users (mode ${mode.toString(8)})`
		);
	}

	const token = contents.trim();
	if (token === '') {
		throw new ServiceCredentialError(`the daemon service token at ${path} is empty`);
	}
	return token;
}

function describe(cause: unknown): string {
	return cause instanceof Error ? cause.message : String(cause);
}

/**
 * Print a one-time bootstrap token for the first administrator.
 *
 * This is a CLI, run once by whoever has local shell access to install this
 * appliance — the same trust boundary `provisioning.ts` already assumes.
 * There is deliberately no route that does this: a fresh admin credential
 * reachable over the network, even loopback-only, would be a wider door than
 * the local shell already is, and the installer already has that door.
 *
 * The database this opens is the real `web.db` the running service reads, not
 * a copy: `issueBootstrapToken` supersedes any outstanding token in place, so
 * running this twice reissues rather than accumulates.
 */

import { openWebDatabase } from '../src/lib/server/auth/database';
import { issueBootstrapToken, isProvisioned } from '../src/lib/server/auth/provisioning';

const databasePath = process.env.CS71_WEB_DATABASE_PATH ?? '/var/lib/cs71-web/web.db';

function main(): number {
	try {
		const database = openWebDatabase(databasePath);
		try {
			if (isProvisioned(database)) {
				console.error(
					`${databasePath} is already provisioned. Create further accounts as an` +
						' administrator instead of reissuing a bootstrap token.'
				);
				return 1;
			}
			const grant = issueBootstrapToken(database, new Date());
			// The token alone on stdout, so it can be captured cleanly; everything an
			// operator needs to read goes to stderr instead.
			console.log(grant.token);
			console.error(`Issued. Expires ${grant.expiresAt} — use it once at the login screen.`);
			return 0;
		} finally {
			database.close();
		}
	} catch (error) {
		console.error(error instanceof Error ? error.message : String(error));
		return 1;
	}
}

process.exitCode = main();

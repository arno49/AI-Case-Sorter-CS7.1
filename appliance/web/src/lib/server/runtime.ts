/**
 * The web service's process-wide resources.
 *
 * `web.db` is opened once and shared: `better-sqlite3` is synchronous and
 * safe across a Node server's request handlers, and a connection per request
 * would multiply file handles for no benefit.
 *
 * Opening is lazy so that importing a module does not touch the filesystem —
 * tests and the build must not create a database as a side effect of loading
 * code.
 */

import { openWebDatabase, type WebDatabase } from './auth/database';
import { loadWebConfig, type WebConfig } from './config';
import { EventBroadcast } from './daemon/broadcast';
import { DaemonClient } from './daemon/client';
import { createWebLimits, type WebLimits } from './limits';

export interface WebRuntime {
	readonly config: Readonly<WebConfig>;
	readonly database: WebDatabase;
	/**
	 * Rate and concurrency budgets. They live here rather than at module scope
	 * so that they start empty with the process, and so a test can discard them
	 * with the rest of the runtime instead of leaking counts into the next one.
	 */
	readonly limits: WebLimits;
	/**
	 * The daemon client. Constructing it touches nothing: the socket is dialled
	 * and the credential is read on the first command, so the web service starts
	 * even when `cs71d` is not up yet and reports that per request instead.
	 */
	readonly daemon: DaemonClient;
	/**
	 * The one reader of the daemon's event stream, shared by every browser.
	 *
	 * It attaches when the first browser subscribes and lets go when the last one
	 * leaves, so an appliance nobody is watching holds no connection open.
	 */
	readonly events: EventBroadcast;
}

let runtime: WebRuntime | undefined;

export function webRuntime(): WebRuntime {
	runtime ??= start();
	return runtime;
}

function start(): WebRuntime {
	const config = loadWebConfig(process.env);
	// Production directories, their ownership and their modes belong to the
	// installer; only development invents them from the service's own umask.
	const database = openWebDatabase(config.databasePath, {
		createDirectory: config.profile !== 'production'
	});
	const daemon = new DaemonClient({
		socketPath: config.daemonSocketPath,
		serviceTokenPath: config.serviceTokenPath
	});
	const events = new EventBroadcast({
		socketPath: config.daemonSocketPath,
		serviceTokenPath: config.serviceTokenPath
	});
	return { config, database, limits: createWebLimits(), daemon, events };
}

/**
 * Let go of everything this process holds.
 *
 * Nothing here reaches the machine. The daemon owns every operation in flight
 * and goes on running it; this closes a database handle and a reader, which is
 * why restarting the web service can neither cancel nor duplicate an operation.
 */
export function closeWebRuntime(): void {
	runtime?.events.close();
	runtime?.database.close();
	runtime = undefined;
}

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

export interface WebRuntime {
	readonly config: Readonly<WebConfig>;
	readonly database: WebDatabase;
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
	return { config, database };
}

export function closeWebRuntime(): void {
	runtime?.database.close();
	runtime = undefined;
}

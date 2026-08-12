export type WebProfile = 'development' | 'test' | 'production';

export interface WebConfig {
	profile: WebProfile;
	daemonSocketPath: string;
	databasePath: string;
	serviceTokenPath: string;
}

const PRODUCTION_SOCKET_PATH = '/run/cs71/cs71d.sock';
const DEVELOPMENT_SOCKET_PATH = '/tmp/cs71/cs71d.sock';
const PRODUCTION_DATABASE_PATH = '/var/lib/cs71-web/web.db';
const DEVELOPMENT_DATABASE_PATH = '/tmp/cs71-web/web.db';
const PRODUCTION_SERVICE_TOKEN_PATH = '/etc/cs71-web/service-token';
const DEVELOPMENT_SERVICE_TOKEN_PATH = '/tmp/cs71-web/service-token';

export function loadWebConfig(
	env: Readonly<Record<string, string | undefined>>
): Readonly<WebConfig> {
	const profile = parseProfile(env.CS71_WEB_PROFILE);
	const daemonSocketPath =
		env.CS71D_SOCKET_PATH ??
		(profile === 'production' ? PRODUCTION_SOCKET_PATH : DEVELOPMENT_SOCKET_PATH);
	const databasePath =
		env.CS71_WEB_DATABASE_PATH ??
		(profile === 'production' ? PRODUCTION_DATABASE_PATH : DEVELOPMENT_DATABASE_PATH);
	// The daemon credential is named here, never carried here: an environment
	// value is readable through the process table by any local user.
	const serviceTokenPath =
		env.CS71_WEB_SERVICE_TOKEN_PATH ??
		(profile === 'production' ? PRODUCTION_SERVICE_TOKEN_PATH : DEVELOPMENT_SERVICE_TOKEN_PATH);

	if (!daemonSocketPath.startsWith('/') || daemonSocketPath.includes('://')) {
		throw new Error('CS71D_SOCKET_PATH must be an absolute Unix socket path');
	}
	if (profile === 'production' && daemonSocketPath !== PRODUCTION_SOCKET_PATH) {
		throw new Error(`production CS71D_SOCKET_PATH must be ${PRODUCTION_SOCKET_PATH}`);
	}
	if (!databasePath.startsWith('/')) {
		throw new Error('CS71_WEB_DATABASE_PATH must be an absolute path');
	}
	// The web database is never the daemon's: separate ownership is the control
	// that keeps a web compromise away from the machine journal.
	if (profile === 'production' && databasePath !== PRODUCTION_DATABASE_PATH) {
		throw new Error(`production CS71_WEB_DATABASE_PATH must be ${PRODUCTION_DATABASE_PATH}`);
	}
	if (!serviceTokenPath.startsWith('/')) {
		throw new Error('CS71_WEB_SERVICE_TOKEN_PATH must be an absolute path');
	}
	if (profile === 'production' && serviceTokenPath !== PRODUCTION_SERVICE_TOKEN_PATH) {
		throw new Error(
			`production CS71_WEB_SERVICE_TOKEN_PATH must be ${PRODUCTION_SERVICE_TOKEN_PATH}`
		);
	}

	return Object.freeze({ profile, daemonSocketPath, databasePath, serviceTokenPath });
}

function parseProfile(value: string | undefined): WebProfile {
	if (value === undefined) {
		return 'development';
	}
	if (value === 'development' || value === 'test' || value === 'production') {
		return value;
	}
	throw new Error(`invalid CS71_WEB_PROFILE: ${value}`);
}

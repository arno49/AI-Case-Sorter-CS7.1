export type WebProfile = 'development' | 'test' | 'production';

export interface WebConfig {
	profile: WebProfile;
	daemonSocketPath: string;
}

const PRODUCTION_SOCKET_PATH = '/run/cs71/cs71d.sock';
const DEVELOPMENT_SOCKET_PATH = '/tmp/cs71/cs71d.sock';

export function loadWebConfig(
	env: Readonly<Record<string, string | undefined>>
): Readonly<WebConfig> {
	const profile = parseProfile(env.CS71_WEB_PROFILE);
	const daemonSocketPath =
		env.CS71D_SOCKET_PATH ??
		(profile === 'production' ? PRODUCTION_SOCKET_PATH : DEVELOPMENT_SOCKET_PATH);

	if (!daemonSocketPath.startsWith('/') || daemonSocketPath.includes('://')) {
		throw new Error('CS71D_SOCKET_PATH must be an absolute Unix socket path');
	}
	if (profile === 'production' && daemonSocketPath !== PRODUCTION_SOCKET_PATH) {
		throw new Error(`production CS71D_SOCKET_PATH must be ${PRODUCTION_SOCKET_PATH}`);
	}

	return Object.freeze({ profile, daemonSocketPath });
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

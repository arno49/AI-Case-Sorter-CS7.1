import { describe, expect, it } from 'vitest';

import { loadWebConfig } from './config';

describe('loadWebConfig', () => {
	it('defaults to a local development socket and database', () => {
		expect(loadWebConfig({})).toEqual({
			profile: 'development',
			daemonSocketPath: '/tmp/cs71/cs71d.sock',
			databasePath: '/tmp/cs71-web/web.db'
		});
	});

	it('uses the fixed production socket and database', () => {
		expect(loadWebConfig({ CS71_WEB_PROFILE: 'production' })).toEqual({
			profile: 'production',
			daemonSocketPath: '/run/cs71/cs71d.sock',
			databasePath: '/var/lib/cs71-web/web.db'
		});
	});

	it('keeps the web database separate from the daemon state directory', () => {
		expect(() =>
			loadWebConfig({
				CS71_WEB_PROFILE: 'production',
				CS71_WEB_DATABASE_PATH: '/var/lib/cs71d/machine.db'
			})
		).toThrow('production CS71_WEB_DATABASE_PATH');
	});

	it('rejects a relative database path', () => {
		expect(() => loadWebConfig({ CS71_WEB_DATABASE_PATH: 'web.db' })).toThrow('absolute path');
	});

	it('rejects a TCP daemon endpoint', () => {
		expect(() =>
			loadWebConfig({
				CS71_WEB_PROFILE: 'production',
				CS71D_SOCKET_PATH: 'http://127.0.0.1:8080'
			})
		).toThrow('absolute Unix socket path');
	});

	it('rejects an arbitrary production socket', () => {
		expect(() =>
			loadWebConfig({
				CS71_WEB_PROFILE: 'production',
				CS71D_SOCKET_PATH: '/tmp/cs71d.sock'
			})
		).toThrow('production CS71D_SOCKET_PATH');
	});
});

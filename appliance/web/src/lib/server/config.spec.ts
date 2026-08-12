import { describe, expect, it } from 'vitest';

import { loadWebConfig } from './config';

describe('loadWebConfig', () => {
	it('defaults to a local development socket, database and credential file', () => {
		expect(loadWebConfig({})).toEqual({
			profile: 'development',
			daemonSocketPath: '/tmp/cs71/cs71d.sock',
			databasePath: '/tmp/cs71-web/web.db',
			serviceTokenPath: '/tmp/cs71-web/service-token',
			visionSocketPath: '/tmp/cs71-vision/cs71vision.sock'
		});
	});

	it('uses the fixed production socket, database and credential file', () => {
		expect(loadWebConfig({ CS71_WEB_PROFILE: 'production' })).toEqual({
			profile: 'production',
			daemonSocketPath: '/run/cs71/cs71d.sock',
			databasePath: '/var/lib/cs71-web/web.db',
			serviceTokenPath: '/etc/cs71-web/service-token',
			visionSocketPath: '/run/cs71-vision/cs71vision.sock'
		});
	});

	it('names the daemon credential file rather than carrying the credential', () => {
		// An environment value is readable through the process table by any local
		// user; the file it names is not.
		expect(Object.keys(loadWebConfig({}))).not.toContain('serviceToken');
	});

	it('rejects an arbitrary production credential file', () => {
		expect(() =>
			loadWebConfig({
				CS71_WEB_PROFILE: 'production',
				CS71_WEB_SERVICE_TOKEN_PATH: '/tmp/token'
			})
		).toThrow('production CS71_WEB_SERVICE_TOKEN_PATH');
	});

	it('rejects a relative credential path', () => {
		expect(() => loadWebConfig({ CS71_WEB_SERVICE_TOKEN_PATH: 'token' })).toThrow(
			'must be an absolute path'
		);
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

	it('rejects a TCP vision endpoint', () => {
		expect(() =>
			loadWebConfig({
				CS71_WEB_PROFILE: 'production',
				CS71_VISION_SOCKET_PATH: 'http://127.0.0.1:8080'
			})
		).toThrow('absolute Unix socket path');
	});

	it('rejects an arbitrary production vision socket', () => {
		expect(() =>
			loadWebConfig({
				CS71_WEB_PROFILE: 'production',
				CS71_VISION_SOCKET_PATH: '/tmp/cs71vision.sock'
			})
		).toThrow('production CS71_VISION_SOCKET_PATH');
	});
});

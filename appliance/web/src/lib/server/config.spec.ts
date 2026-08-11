import { describe, expect, it } from 'vitest';

import { loadWebConfig } from './config';

describe('loadWebConfig', () => {
	it('defaults to a local development socket', () => {
		expect(loadWebConfig({})).toEqual({
			profile: 'development',
			daemonSocketPath: '/tmp/cs71/cs71d.sock'
		});
	});

	it('uses the fixed production socket', () => {
		expect(loadWebConfig({ CS71_WEB_PROFILE: 'production' })).toEqual({
			profile: 'production',
			daemonSocketPath: '/run/cs71/cs71d.sock'
		});
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

import { describe, expect, it } from 'vitest';

import { sessionCookieName, sessionCookieOptions } from './cookies';
import { ABSOLUTE_LIFETIME_MS } from './sessions';

describe('sessionCookieName', () => {
	it('uses the __Host- prefix in production', () => {
		expect(sessionCookieName('production')).toBe('__Host-cs71_session');
	});

	it('drops the prefix where the browser would refuse it', () => {
		// `__Host-` requires Secure, which a plain-HTTP development origin is not.
		expect(sessionCookieName('development')).toBe('cs71_session');
		expect(sessionCookieName('test')).toBe('cs71_session');
	});
});

describe('sessionCookieOptions', () => {
	it('is HttpOnly, SameSite=Strict and root-scoped in every profile', () => {
		for (const profile of ['production', 'development', 'test'] as const) {
			expect(sessionCookieOptions(profile)).toMatchObject({
				path: '/',
				httpOnly: true,
				sameSite: 'strict'
			});
		}
	});

	it('is Secure in production and only there', () => {
		expect(sessionCookieOptions('production').secure).toBe(true);
		expect(sessionCookieOptions('development').secure).toBe(false);
	});

	it('never outlives the session it points at', () => {
		expect(sessionCookieOptions('production').maxAge).toBe(ABSOLUTE_LIFETIME_MS / 1000);
	});

	it('satisfies what the __Host- prefix requires', () => {
		const options = sessionCookieOptions('production');

		expect(options.secure).toBe(true);
		expect(options.path).toBe('/');
		expect(options).not.toHaveProperty('domain');
	});
});

/**
 * The token, the cookie it lives in and the origin rule beside it.
 */

import type { Cookies } from '@sveltejs/kit';
import { describe, expect, it } from 'vitest';

import { setSessionCookie } from './cookies';
import {
	CSRF_FIELD,
	CSRF_HEADER,
	clearAnonymousCsrfCookie,
	csrfCookieName,
	csrfTokenFor,
	csrfTokenMatches,
	establishCsrfToken,
	isStateChanging,
	originIsTrusted,
	presentedCsrfToken
} from './csrf';
import { createToken } from './tokens';

interface Jar extends Cookies {
	readonly store: Map<string, string>;
	readonly setCalls: { name: string; value: string; options: Record<string, unknown> }[];
	readonly deleted: string[];
}

function jar(): Jar {
	const store = new Map<string, string>();
	const setCalls: Jar['setCalls'] = [];
	const deleted: string[] = [];
	return {
		store,
		setCalls,
		deleted,
		get: (name: string) => store.get(name),
		set: (name: string, value: string, options: Record<string, unknown>) => {
			store.set(name, value);
			setCalls.push({ name, value, options });
		},
		delete: (name: string) => {
			store.delete(name);
			deleted.push(name);
		}
	} as unknown as Jar;
}

function post(body: string, contentType = 'application/x-www-form-urlencoded'): Request {
	return new Request('http://localhost/login', {
		method: 'POST',
		headers: { 'content-type': contentType },
		body
	});
}

describe('which requests are checked', () => {
	it('leaves the safe methods alone', () => {
		expect(['GET', 'HEAD', 'OPTIONS', 'get'].map(isStateChanging)).toEqual([
			false,
			false,
			false,
			false
		]);
	});

	it('checks everything else, including methods a form cannot send', () => {
		expect(['POST', 'PUT', 'PATCH', 'DELETE', 'PROPFIND'].map(isStateChanging)).toEqual([
			true,
			true,
			true,
			true,
			true
		]);
	});
});

describe('the token a session carries', () => {
	it('is the same for the same session token', () => {
		const token = createToken();

		expect(csrfTokenFor(token)).toBe(csrfTokenFor(token));
	});

	it('differs between sessions', () => {
		expect(csrfTokenFor(createToken())).not.toBe(csrfTokenFor(createToken()));
	});

	it('does not reveal the session token it came from', () => {
		const token = createToken();

		expect(csrfTokenFor(token)).not.toContain(token);
	});

	it('is derived rather than stored, so it needs no cookie of its own', () => {
		const cookies = jar();
		const token = createToken();
		setSessionCookie(cookies, token, 'development');

		expect(establishCsrfToken(cookies, 'development')).toBe(csrfTokenFor(token));
		expect(cookies.store.has(csrfCookieName('development'))).toBe(false);
	});
});

describe('the token a browser without a session carries', () => {
	it('is issued into a cookie the page cannot read', () => {
		const cookies = jar();

		const issued = establishCsrfToken(cookies, 'development');

		expect(cookies.setCalls[0].options).toMatchObject({
			path: '/',
			httpOnly: true,
			sameSite: 'strict',
			secure: false
		});
		expect(cookies.store.get(csrfCookieName('development'))).toBe(issued);
	});

	it('is stable for the browser that already has one', () => {
		const cookies = jar();
		const first = establishCsrfToken(cookies, 'development');

		expect(establishCsrfToken(cookies, 'development')).toBe(first);
	});

	it('is host-prefixed and secure in production', () => {
		const cookies = jar();

		establishCsrfToken(cookies, 'production');

		expect(cookies.setCalls[0].name).toBe('__Host-cs71_csrf');
		expect(cookies.setCalls[0].options).toMatchObject({ secure: true });
	});

	it('is dropped once a session supersedes it', () => {
		const cookies = jar();
		establishCsrfToken(cookies, 'development');

		clearAnonymousCsrfCookie(cookies, 'development');

		expect(cookies.deleted).toEqual([csrfCookieName('development')]);
	});
});

describe('reading what a request presented', () => {
	it('takes a form field', async () => {
		const body = new URLSearchParams({ [CSRF_FIELD]: 'a-token', username: 'operator' });

		expect(await presentedCsrfToken(post(body.toString()))).toBe('a-token');
	});

	it('takes a header, which a fetch can send and a form cannot', async () => {
		const request = new Request('http://localhost/', {
			method: 'POST',
			headers: { [CSRF_HEADER]: 'a-token' }
		});

		expect(await presentedCsrfToken(request)).toBe('a-token');
	});

	it('leaves the body for the handler that runs next', async () => {
		const request = post(
			new URLSearchParams({ [CSRF_FIELD]: 'a-token', name: 'value' }).toString()
		);

		await presentedCsrfToken(request);

		expect((await request.formData()).get('name')).toBe('value');
	});

	it('reads no token from a body it does not understand', async () => {
		expect(
			await presentedCsrfToken(post('{"csrf_token":"a-token"}', 'application/json'))
		).toBeNull();
	});

	it('reports a body that will not parse as no token rather than as an error', async () => {
		const request = new Request('http://localhost/', {
			method: 'POST',
			headers: { 'content-type': 'multipart/form-data; boundary=nonsense' },
			body: 'not a multipart body'
		});

		expect(await presentedCsrfToken(request)).toBeNull();
	});

	it('finds nothing in a post that carried nothing', async () => {
		expect(await presentedCsrfToken(post('username=operator'))).toBeNull();
	});
});

describe('comparing tokens', () => {
	it('accepts the token that was issued', () => {
		const token = csrfTokenFor(createToken());

		expect(csrfTokenMatches(token, token)).toBe(true);
	});

	it('refuses a different one, and refuses none at all', () => {
		const token = csrfTokenFor(createToken());

		expect(csrfTokenMatches(token, csrfTokenFor(createToken()))).toBe(false);
		expect(csrfTokenMatches(token, null)).toBe(false);
		expect(csrfTokenMatches(token, '')).toBe(false);
	});
});

describe('the origin rule', () => {
	const url = new URL('https://cs71.local/logout');

	function from(origin: string | null): Request {
		return new Request(url, {
			method: 'POST',
			headers: origin === null ? {} : { origin }
		});
	}

	it('accepts the appliance itself', () => {
		expect(originIsTrusted(from('https://cs71.local'), url)).toBe(true);
	});

	it('refuses another site, including one that merely starts the same', () => {
		expect(originIsTrusted(from('https://cs71.local.example.com'), url)).toBe(false);
		expect(originIsTrusted(from('http://cs71.local'), url)).toBe(false);
	});

	it('refuses a request that names no origin at all', () => {
		// Browsers send `Origin` on state-changing requests; excusing its absence
		// would be an exemption an attacker can simply ask for.
		expect(originIsTrusted(from(null), url)).toBe(false);
	});
});

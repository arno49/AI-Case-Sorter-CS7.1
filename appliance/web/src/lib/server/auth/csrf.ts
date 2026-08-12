/**
 * Cross-site request forgery, defended twice.
 *
 * The first defence is the Origin policy: a state-changing request must say it
 * came from this appliance's own origin. Browsers have sent `Origin` on
 * state-changing requests for years, so a missing header is refused rather than
 * excused -- an exemption for "no Origin" is an exemption an attacker can ask
 * for.
 *
 * The second is a token the forging page cannot read. For a signed-in browser
 * it is derived from the session token itself, so there is nothing extra to
 * store: the server already receives the token in the cookie on every request,
 * and HMAC is one way, so a leaked form token does not yield the session. It
 * also rotates with the session for free, and a stolen `web.db` still yields
 * nothing -- the database holds only the session token's digest.
 *
 * A browser with no session gets a random token in its own cookie instead,
 * which the login form echoes back. Signing in is state-changing too: it costs
 * the appliance an Argon2id hash and, if a forged login succeeded, it would
 * leave an operator working inside somebody else's account.
 */

import type { Cookies } from '@sveltejs/kit';
import { createHmac } from 'node:crypto';

import type { WebProfile } from '../config';
import { readSessionCookie } from './cookies';
import { createToken, digestsMatch } from './tokens';

/** Field and header a form or a fetch may carry the token in. */
export const CSRF_FIELD = 'csrf_token';
export const CSRF_HEADER = 'x-csrf-token';

const BASE_COOKIE_NAME = 'cs71_csrf';
const HOST_PREFIXED_COOKIE_NAME = `__Host-${BASE_COOKIE_NAME}`;

/** Domain separation, so the derived token is not a value from anywhere else. */
const DERIVATION_CONTEXT = 'cs71-csrf-v1';

const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function csrfCookieName(profile: WebProfile): string {
	return profile === 'production' ? HOST_PREFIXED_COOKIE_NAME : BASE_COOKIE_NAME;
}

export function isStateChanging(method: string): boolean {
	return !SAFE_METHODS.has(method.toUpperCase());
}

/** The token a session's browser must echo back. */
export function csrfTokenFor(sessionToken: string): string {
	return createHmac('sha256', sessionToken).update(DERIVATION_CONTEXT).digest('base64url');
}

/**
 * The token this request must present, creating one for a browser that has no
 * session yet.
 *
 * Called on every request, before anything is validated, so that the token a
 * page renders and the token the next request is checked against are the same
 * value.
 */
export function establishCsrfToken(cookies: Cookies, profile: WebProfile): string {
	const sessionToken = readSessionCookie(cookies, profile);
	if (sessionToken !== undefined) {
		return csrfTokenFor(sessionToken);
	}

	const name = csrfCookieName(profile);
	const existing = cookies.get(name);
	if (existing) {
		return existing;
	}

	const issued = createToken();
	cookies.set(name, issued, {
		path: '/',
		httpOnly: true,
		sameSite: 'strict',
		secure: profile === 'production',
		// Long enough to fill in a login form, short enough that a token left in
		// a page an hour ago is not still accepted.
		maxAge: 60 * 60
	});
	return issued;
}

/** Drop the anonymous token once a session supersedes it. */
export function clearAnonymousCsrfCookie(cookies: Cookies, profile: WebProfile): void {
	cookies.delete(csrfCookieName(profile), { path: '/' });
}

/**
 * Read the token a request presented.
 *
 * The body is read from a clone so the action that runs next still has one.
 * Only form encodings are parsed; anything else must use the header, because
 * a request whose body we do not understand must not be given the benefit of
 * the doubt.
 */
export async function presentedCsrfToken(request: Request): Promise<string | null> {
	const header = request.headers.get(CSRF_HEADER);
	if (header !== null) {
		return header;
	}

	const contentType = request.headers.get('content-type') ?? '';
	if (!/^(application\/x-www-form-urlencoded|multipart\/form-data)/i.test(contentType)) {
		return null;
	}

	try {
		const value = (await request.clone().formData()).get(CSRF_FIELD);
		return typeof value === 'string' ? value : null;
	} catch {
		// A body that will not parse presented no token; it is not an exception.
		return null;
	}
}

export function csrfTokenMatches(expected: string, presented: string | null): boolean {
	return presented !== null && digestsMatch(expected, presented);
}

/**
 * Whether a state-changing request came from this appliance.
 *
 * `Origin` is compared against the origin the server believes it is serving,
 * which behind Caddy is the one `ORIGIN` names, not whatever a `Host` header
 * claims.
 */
export function originIsTrusted(request: Request, url: URL): boolean {
	const origin = request.headers.get('origin');
	return origin !== null && origin === url.origin;
}

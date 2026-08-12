/**
 * Everything the server decides about a request before it reaches a handler.
 *
 * The session is resolved, the request is authorized, and — if it would change
 * something — its origin, its size, its rate and its CSRF token are checked.
 * All of it denies by default. A route is reachable without a session only if
 * its policy says `public`, a signed-in account reaches it only if the policy
 * names a capability the role holds, and a route with no policy at all is
 * refused, so a page added later is protected by omission rather than exposed
 * by it.
 *
 * A state-changing request is checked before authorization, and the checks run
 * cheapest first: a forged or oversized request should cost this appliance a
 * header comparison, not a database read or an Argon2id hash.
 *
 * The login redirect carries only a fixed reason code and never a return path:
 * an attacker-supplied `next` parameter is how a login page becomes an open
 * redirect, and an appliance with one meaningful landing page does not need it.
 */

import { error, redirect, type Handle, type RequestEvent } from '@sveltejs/kit';

import { requireRouteAccess } from '$lib/server/auth/authorization';
import { authenticateRequest } from '$lib/server/auth/boundary';
import {
	csrfTokenMatches,
	establishCsrfToken,
	isStateChanging,
	originIsTrusted,
	presentedCsrfToken
} from '$lib/server/auth/csrf';
import { routePolicy } from '$lib/server/auth/policy';
import { declaresOversizedBody, type WebLimits } from '$lib/server/limits';
import { webRuntime } from '$lib/server/runtime';

const CROSS_SITE = 'This request did not come from the appliance.';
const TOO_LARGE = 'That submission is larger than this appliance accepts.';
const TOO_MANY = 'Too many requests. Wait a moment and try again.';

export const handle: Handle = async ({ event, resolve }) => {
	const { config, database, limits } = webRuntime();
	const now = new Date();
	const authentication = authenticateRequest(database, event.cookies, config.profile, now);

	event.locals.user = authentication.authenticated ? authentication.user : null;
	event.locals.session = authentication.authenticated ? authentication.session : null;
	// Established before anything is validated, so the token a page renders is
	// the token the next request is checked against.
	event.locals.csrfToken = establishCsrfToken(event.cookies, config.profile);

	if (isStateChanging(event.request.method)) {
		await guardStateChange(event, limits, now);
	}

	const policy = routePolicy(event.route.id);
	if (policy?.access === 'public') {
		return resolve(event);
	}

	if (!authentication.authenticated) {
		const reason = authentication.rejection;
		redirect(303, reason === undefined ? '/login' : `/login?reason=${reason}`);
	}

	// A path that matched no route has no handler to authorize; SvelteKit
	// answers it below. Refusing here would turn every missing page into a
	// permission problem for the person reading the screen.
	if (event.route.id !== null) {
		requireRouteAccess(authentication.user, policy);
	}

	return resolve(event);
};

async function guardStateChange(event: RequestEvent, limits: WebLimits, now: Date): Promise<void> {
	if (!originIsTrusted(event.request, event.url)) {
		error(403, CROSS_SITE);
	}
	if (declaresOversizedBody(event.request)) {
		error(413, TOO_LARGE);
	}
	if (!limits.stateChanges.check(event.getClientAddress(), now).allowed) {
		error(429, TOO_MANY);
	}
	if (!csrfTokenMatches(event.locals.csrfToken, await presentedCsrfToken(event.request))) {
		error(403, CROSS_SITE);
	}
}

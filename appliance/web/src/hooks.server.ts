/**
 * Resolve the session on every request, then authorize it, before any page or
 * action runs.
 *
 * Both decisions are made here on the server and both deny by default. A route
 * is reachable without a session only if its policy says `public`, and a
 * signed-in account reaches it only if the policy names a capability the role
 * holds. A route with no policy at all is refused, so a new page is protected
 * by omission rather than exposed by it.
 *
 * The redirect carries only a fixed reason code and never a return path: an
 * attacker-supplied `next` parameter is how a login page becomes an open
 * redirect, and an appliance with one meaningful landing page does not need it.
 */

import { redirect, type Handle } from '@sveltejs/kit';

import { requireRouteAccess } from '$lib/server/auth/authorization';
import { authenticateRequest } from '$lib/server/auth/boundary';
import { routePolicy } from '$lib/server/auth/policy';
import { webRuntime } from '$lib/server/runtime';

export const handle: Handle = async ({ event, resolve }) => {
	const { config, database } = webRuntime();
	const authentication = authenticateRequest(database, event.cookies, config.profile, new Date());

	event.locals.user = authentication.authenticated ? authentication.user : null;
	event.locals.session = authentication.authenticated ? authentication.session : null;

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

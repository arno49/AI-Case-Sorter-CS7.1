/**
 * Resolve the session on every request, and deny by default.
 *
 * Authorization is decided here on the server, before any page or action runs.
 * A route is reachable without a session only if it appears in `PUBLIC_ROUTES`,
 * so a new page is protected by omission rather than exposed by it.
 *
 * The redirect carries only a fixed reason code and never a return path: an
 * attacker-supplied `next` parameter is how a login page becomes an open
 * redirect, and an appliance with one meaningful landing page does not need it.
 */

import { redirect, type Handle } from '@sveltejs/kit';

import { authenticateRequest } from '$lib/server/auth/boundary';
import { webRuntime } from '$lib/server/runtime';

const PUBLIC_ROUTES = new Set(['/login']);

export const handle: Handle = async ({ event, resolve }) => {
	const { config, database } = webRuntime();
	const authentication = authenticateRequest(database, event.cookies, config.profile, new Date());

	event.locals.user = authentication.authenticated ? authentication.user : null;
	event.locals.session = authentication.authenticated ? authentication.session : null;

	if (!authentication.authenticated && !PUBLIC_ROUTES.has(event.url.pathname)) {
		const reason = authentication.rejection;
		redirect(303, reason === undefined ? '/login' : `/login?reason=${reason}`);
	}

	return resolve(event);
};

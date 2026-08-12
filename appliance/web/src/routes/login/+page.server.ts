/**
 * The login form.
 *
 * Every failure answers with the same message. Distinguishing "no such user"
 * from "wrong password" from "account disabled" would turn this form into a
 * directory of who works here, and `authenticate` already spends equal hashing
 * effort on each so the timing does not give it away either.
 *
 * SvelteKit rejects a cross-origin form POST before this action runs. Per-session
 * CSRF tokens and rate limiting are PI-WEB-002 and are not implemented here.
 */

import { fail, redirect, type Actions, type ServerLoad } from '@sveltejs/kit';

import { startSession } from '$lib/server/auth/boundary';
import { authenticate } from '$lib/server/auth/users';
import { webRuntime } from '$lib/server/runtime';

const REJECTED = 'Incorrect username or password.';

const REASONS: Readonly<Record<string, string>> = {
	expired: 'Your session expired. Sign in again.',
	revoked: 'Your session was ended. Sign in again.',
	user_disabled: 'That account is no longer active.',
	unknown: 'Your session is no longer valid. Sign in again.'
};

export const load: ServerLoad = ({ locals, url }) => {
	if (locals.user) {
		redirect(303, '/');
	}
	const reason = url.searchParams.get('reason');
	// Only a known code produces a message; the query string never reaches the page.
	return { notice: reason !== null ? (REASONS[reason] ?? null) : null };
};

export const actions: Actions = {
	default: async ({ request, cookies }) => {
		const form = await request.formData();
		const username = form.get('username');
		const password = form.get('password');
		if (typeof username !== 'string' || typeof password !== 'string') {
			return fail(400, { error: REJECTED });
		}

		const { config, database } = webRuntime();
		const outcome = await authenticate(database, { username, password });
		if (!outcome.ok) {
			return fail(401, { error: REJECTED });
		}

		startSession(database, cookies, outcome.user.userId, config.profile, new Date());
		redirect(303, '/');
	}
};

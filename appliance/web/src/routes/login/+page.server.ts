/**
 * The login form.
 *
 * Every failure answers with the same message. Distinguishing "no such user"
 * from "wrong password" from "account disabled" would turn this form into a
 * directory of who works here, and `authenticate` already spends equal hashing
 * effort on each so the timing does not give it away either.
 *
 * The hook has already refused a cross-origin post and checked the CSRF token
 * the form carries. What is left here is the cost of guessing: attempts are
 * limited per account and per address, and the number of password hashes in
 * flight at once is capped, because each one costs 64 MiB on a Raspberry Pi.
 * Being throttled is reported as such rather than as a wrong password — the
 * operator has not made a mistake and telling them so would be a lie.
 */

import { fail, redirect, type Actions, type ServerLoad } from '@sveltejs/kit';

import { startSession } from '$lib/server/auth/boundary';
import { clearAnonymousCsrfCookie } from '$lib/server/auth/csrf';
import { authenticate } from '$lib/server/auth/users';
import { TooBusyError } from '$lib/server/limits';
import { webRuntime } from '$lib/server/runtime';

const REJECTED = 'Incorrect username or password.';

const THROTTLED = 'Too many sign-in attempts. Wait a minute and try again.';

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
	return {
		notice: reason !== null ? (REASONS[reason] ?? null) : null,
		csrfToken: locals.csrfToken
	};
};

export const actions: Actions = {
	default: async (event) => {
		const form = await event.request.formData();
		const username = form.get('username');
		const password = form.get('password');
		if (typeof username !== 'string' || typeof password !== 'string') {
			return fail(400, { error: REJECTED });
		}

		const { config, database, limits } = webRuntime();
		const now = new Date();
		// Both keys are counted, not just the first to refuse: a spray across
		// accounts and a guess against one account each have their own budget.
		const keys = [`address:${event.getClientAddress()}`, `user:${accountKey(username)}`];
		const decisions = keys.map((key) => limits.logins.check(key, now));
		if (decisions.some((decision) => !decision.allowed)) {
			return fail(429, { error: THROTTLED });
		}

		let outcome;
		try {
			outcome = await limits.loginWork.run(() => authenticate(database, { username, password }));
		} catch (raised) {
			if (raised instanceof TooBusyError) {
				return fail(429, { error: THROTTLED });
			}
			throw raised;
		}
		if (!outcome.ok) {
			return fail(401, { error: REJECTED });
		}

		// Succeeding clears the budget: an operator who mistyped once and then
		// signed in is not still being punished a minute later.
		for (const key of keys) {
			limits.logins.forget(key);
		}

		startSession(database, event.cookies, outcome.user.userId, config.profile, now);
		// The session's own token now derives the CSRF token; the anonymous one
		// would only linger.
		clearAnonymousCsrfCookie(event.cookies, config.profile);
		redirect(303, '/');
	}
};

/** A bounded, case-insensitive key: the submitted username is untrusted input. */
function accountKey(username: string): string {
	return username.toLowerCase().slice(0, 64);
}

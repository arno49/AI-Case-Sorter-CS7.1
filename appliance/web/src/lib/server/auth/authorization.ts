/**
 * Turning a policy into an answer.
 *
 * Authorization is decided on the server before the effect, never by the page
 * that drew the button. These functions raise SvelteKit errors rather than
 * returning a verdict, so a caller that forgets to look at a result cannot
 * proceed unauthorized.
 *
 * A refusal says only that the account is not permitted. Naming the capability
 * would tell an account that guessed a URL what the URL is for, and the person
 * reading it cannot act on the name anyway.
 */

import { error } from '@sveltejs/kit';

import { can, type Capability } from './capabilities';
import type { RoutePolicy } from './policy';
import type { UserRecord } from './users';

export const UNAUTHENTICATED = 'Sign in to continue.';

export const FORBIDDEN = 'Your account is not permitted to do that.';

/**
 * Require a capability of the signed-in account.
 *
 * Handlers call this before acting even when the route table already gated the
 * page: an action is where privilege is spent, and the check that matters is
 * the one next to the effect.
 */
export function requireCapability(
	user: UserRecord | null | undefined,
	capability: Capability
): UserRecord {
	if (!user) {
		// The hook redirects an unauthenticated browser long before this. Reaching
		// here means a handler was called some other way, so it fails rather than
		// treating a missing user as an anonymous one.
		error(401, UNAUTHENTICATED);
	}
	if (!can(user.role, capability)) {
		error(403, FORBIDDEN);
	}
	return user;
}

/** Apply a route's declared policy to a signed-in account. */
export function requireRouteAccess(user: UserRecord, policy: RoutePolicy | undefined): UserRecord {
	if (policy === undefined) {
		// A route nobody declared is not a route anybody may reach.
		error(403, FORBIDDEN);
	}
	if (policy.access === 'capability') {
		return requireCapability(user, policy.capability);
	}
	return user;
}

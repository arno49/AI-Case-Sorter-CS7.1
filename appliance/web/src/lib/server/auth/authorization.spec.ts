/**
 * Refusals, and what they say.
 *
 * The interesting property is not that an allowed call returns; it is that a
 * refused one raises instead of returning something a caller could ignore.
 */

import { isHttpError } from '@sveltejs/kit';
import { describe, expect, it } from 'vitest';

import { FORBIDDEN, UNAUTHENTICATED, requireCapability, requireRouteAccess } from './authorization';
import type { RoutePolicy } from './policy';
import type { Role, UserRecord } from './users';

function accountWith(role: Role): UserRecord {
	return {
		userId: 'user_0123456789abcdef',
		username: role,
		role,
		createdAt: '2026-08-11T12:00:00.000Z',
		passwordChangedAt: '2026-08-11T12:00:00.000Z',
		disabledAt: null
	};
}

/** The status and message of the error a call raised, or `null` if it returned. */
function refusal(call: () => unknown): { status: number; message: string } | null {
	try {
		call();
		return null;
	} catch (raised) {
		if (isHttpError(raised)) {
			return { status: raised.status, message: raised.body.message };
		}
		throw raised;
	}
}

describe('requiring a capability', () => {
	it('returns the account that holds it', () => {
		const operator = accountWith('operator');

		expect(requireCapability(operator, 'machine.operate')).toBe(operator);
	});

	it('refuses an account that does not hold it', () => {
		expect(refusal(() => requireCapability(accountWith('viewer'), 'machine.operate'))).toEqual({
			status: 403,
			message: FORBIDDEN
		});
	});

	it('refuses an administrator the same as anyone else for an ungranted capability', () => {
		expect(
			refusal(() => requireCapability(accountWith('administrator'), 'protocol.direct'))
		).toEqual({ status: 403, message: FORBIDDEN });
	});

	it('names neither the capability nor the route in the refusal', () => {
		const refused = refusal(() => requireCapability(accountWith('viewer'), 'users.manage'));

		expect(refused?.message).not.toContain('users.manage');
	});

	it('refuses rather than treating a missing account as anonymous', () => {
		expect(refusal(() => requireCapability(null, 'machine.read'))).toEqual({
			status: 401,
			message: UNAUTHENTICATED
		});
	});
});

describe('applying a route policy', () => {
	const capability: RoutePolicy = { access: 'capability', capability: 'machine.operate' };

	it('admits any signed-in account to an authenticated route', () => {
		expect(requireRouteAccess(accountWith('viewer'), { access: 'authenticated' }).role).toBe(
			'viewer'
		);
	});

	it('admits a role that holds the declared capability', () => {
		expect(requireRouteAccess(accountWith('operator'), capability).role).toBe('operator');
	});

	it('refuses a role that does not', () => {
		expect(refusal(() => requireRouteAccess(accountWith('viewer'), capability))).toEqual({
			status: 403,
			message: FORBIDDEN
		});
	});

	it('refuses a route nobody declared a policy for', () => {
		// A page added without an entry is unreachable rather than open.
		expect(refusal(() => requireRouteAccess(accountWith('administrator'), undefined))).toEqual({
			status: 403,
			message: FORBIDDEN
		});
	});
});

/**
 * What each route requires, declared in one table.
 *
 * The hook reads this before any page or action runs, so a route's access rule
 * is visible in a single file rather than spread across the handlers that
 * happen to remember to check. A route with no entry is unreachable: adding a
 * page without declaring who may reach it fails closed, and `policy.spec.ts`
 * fails the build rather than waiting for a request to discover it.
 *
 * The entry is the coarse gate. A route whose actions differ in privilege --
 * a page an operator may read but only an administrator may submit -- still
 * calls `requireCapability` inside the action; the table cannot express a
 * per-action rule and should not pretend to.
 */

import type { Capability } from './capabilities';

export type RoutePolicy =
	/** Reachable without a session. Keep this list as short as signing in. */
	| { readonly access: 'public' }
	/** Any signed-in account, regardless of role. */
	| { readonly access: 'authenticated' }
	| { readonly access: 'capability'; readonly capability: Capability };

export type RoutePolicies = Readonly<Record<string, RoutePolicy>>;

/** Keyed by SvelteKit route id, which is the route directory, not the URL. */
export const ROUTE_POLICIES: RoutePolicies = Object.freeze({
	'/': { access: 'capability', capability: 'machine.read' },
	/** The machine's event stream. Watching is reading, and nothing more. */
	'/events': { access: 'capability', capability: 'machine.read' },
	/** The durable operation history. A read of the same record `machine.read`
	 *  already grants a view of on the dashboard, just further back. */
	'/operations': { access: 'capability', capability: 'machine.read' },
	/** Versions, journal-evidence and DTR-gate status. A read, same as the
	 *  dashboard's snapshot; nothing here is a command. */
	'/system': { access: 'capability', capability: 'machine.read' },
	'/login': { access: 'public' },
	'/logout': { access: 'authenticated' }
});

export function routePolicy(
	routeId: string | null,
	policies: RoutePolicies = ROUTE_POLICIES
): RoutePolicy | undefined {
	return routeId === null ? undefined : policies[routeId];
}

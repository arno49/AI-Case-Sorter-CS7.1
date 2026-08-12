/**
 * The route table, and the guarantee that it covers the routes that exist.
 *
 * The scan below is the reason the table can be trusted as a list of everything
 * reachable: a page added without an entry fails this spec, rather than waiting
 * for someone to notice that the hook refuses it in production.
 */

import { readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { describe, expect, it } from 'vitest';

import { ROUTE_POLICIES, routePolicy } from './policy';

const ROUTES_ROOT = join(process.cwd(), 'src', 'routes');

const ROUTE_FILES = ['+page.svelte', '+page.server.ts', '+server.ts'];

/** Every route id SvelteKit will produce from the files on disk. */
function declaredRoutes(): string[] {
	const directories = new Set<string>();
	for (const entry of readdirSync(ROUTES_ROOT, { recursive: true, withFileTypes: true })) {
		if (entry.isFile() && ROUTE_FILES.includes(entry.name)) {
			directories.add(entry.parentPath);
		}
	}
	return [...directories].map(toRouteId).sort();
}

function toRouteId(directory: string): string {
	const segments = relative(ROUTES_ROOT, directory)
		.split(sep)
		.filter((segment) => segment !== '' && !/^\(.*\)$/.test(segment));
	return segments.length === 0 ? '/' : `/${segments.join('/')}`;
}

describe('route coverage', () => {
	it('declares a policy for every route that exists', () => {
		expect(declaredRoutes().filter((id) => !(id in ROUTE_POLICIES))).toEqual([]);
	});

	it('declares no policy for a route that does not', () => {
		const routes = new Set(declaredRoutes());

		expect(Object.keys(ROUTE_POLICIES).filter((id) => !routes.has(id))).toEqual([]);
	});

	it('found the routes at all, so an empty scan cannot pass the checks above', () => {
		expect(declaredRoutes()).toContain('/login');
	});
});

describe('what the table says today', () => {
	it('keeps the dashboard behind a capability rather than mere sign-in', () => {
		expect(routePolicy('/')).toEqual({ access: 'capability', capability: 'machine.read' });
	});

	it('makes signing in the only public route', () => {
		const publicRoutes = Object.entries(ROUTE_POLICIES)
			.filter(([, policy]) => policy.access === 'public')
			.map(([id]) => id);

		expect(publicRoutes).toEqual(['/login']);
	});

	it('lets any signed-in account sign out, whatever its role', () => {
		expect(routePolicy('/logout')).toEqual({ access: 'authenticated' });
	});
});

describe('looking up a route', () => {
	it('has nothing to say about a path that matched no route', () => {
		expect(routePolicy(null)).toBeUndefined();
	});

	it('has nothing to say about a route it was never told about', () => {
		expect(routePolicy('/machine/stop')).toBeUndefined();
	});

	it('reads from the table it was given, so the hook can be tested', () => {
		const policies = {
			'/machine': { access: 'capability', capability: 'machine.operate' }
		} as const;

		expect(routePolicy('/machine', policies)).toEqual({
			access: 'capability',
			capability: 'machine.operate'
		});
	});
});

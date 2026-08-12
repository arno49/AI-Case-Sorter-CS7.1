/**
 * A simulated browser for the route specs.
 *
 * These build the parts of a SvelteKit request the server code actually reads,
 * so the hook and the actions under test are the real ones rather than copies
 * of their logic.
 */

import {
	isHttpError,
	isRedirect,
	type Cookies,
	type Handle,
	type RequestEvent
} from '@sveltejs/kit';

import { establishCsrfToken } from '$lib/server/auth/csrf';
import type { WebProfile } from '$lib/server/config';

export interface CookieJar extends Cookies {
	readonly store: Map<string, string>;
}

/**
 * The token this browser's next post must carry.
 *
 * Idempotent: it derives the token from a session cookie when there is one and
 * otherwise returns the anonymous token already in the jar, which is what a
 * page rendered for this browser would have shown.
 */
export function csrfFor(cookies: CookieJar, profile: WebProfile = 'development'): string {
	return establishCsrfToken(cookies, profile);
}

/** A cookie jar shared across the requests of one simulated browser. */
export function browser(): CookieJar {
	const store = new Map<string, string>();
	return {
		store,
		get: (name: string) => store.get(name),
		getAll: () => [...store].map(([name, value]) => ({ name, value })),
		set: (name: string, value: string) => store.set(name, value),
		delete: (name: string) => store.delete(name),
		serialize: () => ''
	} as unknown as CookieJar;
}

export interface RequestOptions {
	readonly form?: Record<string, string>;
	/**
	 * The matched route, which is not always the path: `null` is a path that
	 * matched nothing, and a route id with no page on disk stands in for one
	 * added without a policy.
	 */
	readonly routeId?: string | null;
	readonly method?: string;
	/** `null` sends no `Origin` header at all, as a forged post would not. */
	readonly origin?: string | null;
	readonly headers?: Record<string, string>;
	readonly address?: string;
}

/**
 * A request built from a real `Request`.
 *
 * The hook reads the method, the headers and a clone of the body, so a stubbed
 * object with a `formData` method would not exercise what production does.
 */
export function request(
	path: string,
	cookies: Cookies,
	options: RequestOptions = {}
): RequestEvent {
	const url = new URL(`http://localhost${path}`);
	const method = options.method ?? (options.form === undefined ? 'GET' : 'POST');
	const headers = new Headers(options.headers);
	const origin = options.origin === undefined ? url.origin : options.origin;
	if (origin !== null) {
		headers.set('origin', origin);
	}

	const body =
		options.form === undefined ? undefined : new URLSearchParams(Object.entries(options.form));

	return {
		cookies,
		url,
		route: { id: options.routeId === undefined ? url.pathname : options.routeId },
		locals: {},
		getClientAddress: () => options.address ?? '10.0.0.2',
		request: new Request(url, { method, headers, body })
	} as unknown as RequestEvent;
}

export type HookOutcome =
	| { readonly redirectedTo: string }
	| { readonly refused: number }
	| { readonly allowed: RequestEvent };

/** Run the hook, reporting what it did with the request. */
export async function throughHook(event: RequestEvent, handle: Handle): Promise<HookOutcome> {
	const resolve = (async () => new Response('ok')) as Parameters<Handle>[0]['resolve'];
	try {
		await handle({ event, resolve });
	} catch (raised) {
		if (isRedirect(raised)) {
			return { redirectedTo: raised.location };
		}
		if (isHttpError(raised)) {
			return { refused: raised.status };
		}
		throw raised;
	}
	return { allowed: event };
}

export async function raisedBy(action: () => unknown): Promise<unknown> {
	try {
		return await action();
	} catch (raised) {
		return raised;
	}
}

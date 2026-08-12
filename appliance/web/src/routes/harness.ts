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

export interface CookieJar extends Cookies {
	readonly store: Map<string, string>;
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
}

export function request(
	path: string,
	cookies: Cookies,
	options: RequestOptions = {}
): RequestEvent {
	const url = new URL(`http://localhost${path}`);
	return {
		cookies,
		url,
		route: { id: options.routeId === undefined ? url.pathname : options.routeId },
		locals: {},
		request: {
			formData: async () => {
				const data = new FormData();
				for (const [key, value] of Object.entries(options.form ?? {})) {
					data.set(key, value);
				}
				return data;
			}
		}
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

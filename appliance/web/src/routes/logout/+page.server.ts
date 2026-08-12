/**
 * Signing out.
 *
 * Only a POST ends a session. A `GET /logout` would let any page on the
 * network sign an operator out with an image tag, which is a denial of control
 * on a machine that may be running.
 */

import { redirect, type Actions, type ServerLoad } from '@sveltejs/kit';

import { endSession } from '$lib/server/auth/boundary';
import { webRuntime } from '$lib/server/runtime';

export const load: ServerLoad = () => {
	redirect(303, '/');
};

export const actions: Actions = {
	default: ({ cookies }) => {
		const { config, database } = webRuntime();
		endSession(database, cookies, config.profile, new Date());
		redirect(303, '/login');
	}
};

import type { ServerLoad } from '@sveltejs/kit';

import { capabilitiesFor } from '$lib/server/auth/capabilities';

export const load: ServerLoad = ({ locals }) => {
	// The hook has already refused an unauthenticated request and checked that
	// this role may read the machine; this page only reports who the server
	// decided the operator is and what it will let them do. The list drives what
	// the page offers, but every request is authorized again server-side.
	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null,
		capabilities: locals.user === null ? [] : capabilitiesFor(locals.user.role),
		csrfToken: locals.csrfToken
	};
};

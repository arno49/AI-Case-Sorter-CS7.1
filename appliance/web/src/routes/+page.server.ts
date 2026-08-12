import type { ServerLoad } from '@sveltejs/kit';

export const load: ServerLoad = ({ locals }) => {
	// The hook has already refused an unauthenticated request; this page only
	// reports who the server decided the operator is.
	return {
		username: locals.user?.username ?? null,
		role: locals.user?.role ?? null
	};
};

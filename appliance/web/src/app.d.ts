// See https://svelte.dev/docs/kit/types#app.d.ts
// for information about these interfaces
import type { SessionRecord } from '$lib/server/auth/sessions';
import type { UserRecord } from '$lib/server/auth/users';

declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			/** The authenticated operator, or `null` on a public route. */
			user: UserRecord | null;
			session: SessionRecord | null;
			/** The token every state-changing form on this response must carry. */
			csrfToken: string;
		}
		// interface PageData {}
		// interface PageState {}
		// interface Platform {}
	}
}

export {};

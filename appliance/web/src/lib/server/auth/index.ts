/**
 * Server-side authentication core.
 *
 * Everything here runs only on the SvelteKit server: `$lib/server` is never
 * bundled into the browser, so no hashing parameter, session row or token
 * digest can reach client code by accident.
 */

export {
	IN_MEMORY,
	MIGRATIONS,
	SCHEMA_VERSION,
	WebDatabaseError,
	WebSchemaError,
	migrationChecksum,
	openWebDatabase,
	schemaVersion,
	type Migration,
	type OpenOptions,
	type WebDatabase
} from './database';

export {
	MAXIMUM_PASSWORD_LENGTH,
	MINIMUM_PASSWORD_LENGTH,
	PASSWORD_HASH_POLICY,
	WeakPasswordError,
	assertUsablePassword,
	hashPassword,
	needsRehash,
	spendVerificationEffort,
	verifyPassword
} from './passwords';

export { TOKEN_BYTES, createIdentifier, createToken, digestsMatch, hashToken } from './tokens';

export {
	DuplicateUsernameError,
	InvalidRoleError,
	InvalidUsernameError,
	MAXIMUM_USERNAME_LENGTH,
	MINIMUM_USERNAME_LENGTH,
	ROLES,
	UnknownUserError,
	authenticate,
	changePassword,
	createUser,
	findUserById,
	findUserByUsername,
	insertUser,
	listUsers,
	normalizeUsername,
	prepareUser,
	setUserDisabled,
	userCount,
	type AuthenticationOutcome,
	type CreateUserOptions,
	type PreparedUser,
	type Role,
	type UserRecord
} from './users';

export {
	ABSOLUTE_LIFETIME_MS,
	IDLE_TIMEOUT_MS,
	activeSessions,
	issueSession,
	pruneExpiredSessions,
	resolveSession,
	revokeSession,
	revokeSessionByToken,
	revokeSessionsForUser,
	type IssueSessionOptions,
	type IssuedSession,
	type ResolvedSession,
	type RevocationReason,
	type SessionRecord,
	type SessionRejection
} from './sessions';

export {
	clearSessionCookie,
	readSessionCookie,
	sessionCookieName,
	sessionCookieOptions,
	setSessionCookie,
	type SessionCookieOptions
} from './cookies';

export {
	authenticateRequest,
	endSession,
	startSession,
	type AuthenticatedRequest,
	type RequestAuthentication
} from './boundary';

export {
	BOOTSTRAP_TOKEN_LIFETIME_MS,
	ProvisioningError,
	claimBootstrapToken,
	isProvisioned,
	issueBootstrapToken,
	type BootstrapGrant,
	type ClaimOptions,
	type ClaimOutcome
} from './provisioning';

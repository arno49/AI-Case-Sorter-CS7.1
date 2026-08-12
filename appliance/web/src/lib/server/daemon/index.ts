/**
 * The BFF's side of the daemon boundary.
 *
 * Everything here runs only on the server. The browser never learns the socket
 * path, the service credential or the wire vocabulary; it submits an intent and
 * is told what this workspace decided to say about the answer.
 */

export {
	API_VERSION,
	DEADLINES,
	DaemonClient,
	InvalidCommandError,
	actorFor,
	newIdempotencyKey,
	type Actor,
	type CommandContext,
	type Configuration,
	type ConfigurationPatch,
	type DaemonClientOptions,
	type HomeTarget,
	type MachineSnapshot,
	type Operation,
	type OperationAccepted
} from './client';

export { ServiceCredentialError, readServiceToken } from './credentials';

export {
	DaemonError,
	safeResponseFor,
	type DaemonErrorCode,
	type DaemonFailureKind,
	type SafeResponse
} from './errors';

export {
	MAXIMUM_RESPONSE_BYTES,
	exchange,
	type DaemonExchange,
	type DaemonReply
} from './transport';

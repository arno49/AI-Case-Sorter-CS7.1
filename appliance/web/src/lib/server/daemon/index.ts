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

export {
	EventBroadcast,
	REPLAY_CAPACITY,
	SUBSCRIBER_BACKLOG_LIMIT,
	type BrowserMessage,
	type EventBroadcastOptions,
	type ResyncReason,
	type SubscribeOptions,
	type UpstreamSource
} from './broadcast';

export { ServiceCredentialError, readServiceToken } from './credentials';

export {
	IDLE_TIMEOUT_MS,
	MAXIMUM_EVENT_BYTES,
	RESYNC_EVENT,
	readDaemonEvents,
	subscribeToDaemonEvents,
	type DaemonEvent,
	type EventStreamOptions,
	type StreamMessage,
	type SubscriptionOptions
} from './events';

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

/**
 * The BFF's side of the `cs71-vision` boundary.
 *
 * Everything here runs only on the server. The browser never learns the
 * socket path, the service credential or the wire shape - it is told what
 * this workspace decided to say about the dataset review numbers.
 */

export {
	API_VERSION,
	DEADLINES,
	InvalidCommandError,
	VisionClient,
	type ActivationResult,
	type AutonomousAccuracy,
	type AutonomousReviewResult,
	type AutonomySummary,
	type CandidateSummary,
	type DatasetClassSummary,
	type DatasetSummary,
	type ModelsSummary,
	type PendingAutonomousReview,
	type RoutingLegendEntry,
	type RoutingProfileRequest,
	type RoutingState,
	type Suggestion,
	type SuggestionAccuracy,
	type SuggestionResult,
	type TrainResult,
	type VisionClientOptions
} from './client';

export {
	VisionError,
	safeResponseFor,
	type SafeVisionResponse,
	type VisionFailureKind
} from './errors';

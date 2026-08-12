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
	VisionClient,
	type DatasetClassSummary,
	type DatasetSummary,
	type VisionClientOptions
} from './client';

export {
	VisionError,
	safeResponseFor,
	type SafeVisionResponse,
	type VisionFailureKind
} from './errors';

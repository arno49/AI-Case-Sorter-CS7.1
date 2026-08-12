/**
 * The vision dataset read model, named once.
 *
 * Mirrors `machine.ts`'s role for the daemon contract: a place both the
 * server and a screen can reach these types from, kept separate from
 * `$lib/server/vision` itself. Unlike `machine.ts` these are not generated -
 * `cs71-vision`'s dataset api (PI-VISION-003) is one read-only resource with
 * no OpenAPI contract of its own - so this is a hand-written alias, not a
 * transcription of a schema. `client.ts` is still the one place that parses
 * the wire response; nothing here re-derives that shape.
 */

export type { DatasetClassSummary, DatasetSummary } from '$lib/server/vision';

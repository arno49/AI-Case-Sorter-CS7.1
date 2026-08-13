/**
 * The vision routing read model, named once.
 *
 * Mirrors `dataset.ts`'s own role: a place both the server and a screen can
 * reach these types from, kept separate from `$lib/server/vision` itself.
 * `client.ts` is still the one place that parses the wire response; nothing
 * here re-derives that shape.
 */

export type { RoutingLegendEntry, RoutingProfileRequest, RoutingState } from '$lib/server/vision';

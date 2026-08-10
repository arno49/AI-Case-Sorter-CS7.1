# ADR-0012: Versioned operations, events and idempotency

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

Retrying network/BFF requests and reconnecting event streams must not duplicate machine intent or confuse firmware protocol correlation.

## Decision

Expose versioned `/v1` operations with UUID `operation_id`, daemon `event_id`, snapshot generation, finite deadlines and idempotency keys. Keep all separate from protocol `request_id`.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Durable operations/events/idempotency | Selected: makes retries and audit explicit. |
| Fire-and-forget commands | Rejected: no trustworthy lifecycle or deduplication. |
| Use protocol request_id as API ID | Rejected: session-scoped/wrapping wire correlation. |

## Consequences

### Positive

- Client can query durable outcome and reconcile SSE loss.
- Stale UI commands are rejected before serial I/O.

### Negative

- Requires retention/pruning and canonical request fingerprints.

## Implementation constraints

- Success requires trusted terminal; 202 is acceptance only.
- Stale event cursor emits snapshot-required rather than silent loss.

## Validation and revisit triggers

- Test duplicate keys, conflicting keys, deadlines, stale generations, overflow and request-id wrap.
- Revisit for a new API major or retention scale requirement, preserving ID separation.

## Links

- [API](../api-and-events.md); [Runtime](../runtime-and-domain.md); [PI-DOMAIN-001](../backlog.md#pi-domain-001--implement-operations-idempotency-and-snapshot-generation).

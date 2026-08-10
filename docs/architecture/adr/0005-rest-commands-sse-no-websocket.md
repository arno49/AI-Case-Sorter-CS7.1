# ADR-0005: REST commands plus SSE, no WebSocket for MVP

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

Machine commands are request/response operations while updates are predominantly server-to-browser. Duplex WebSocket complexity is not justified for MVP.

## Decision

Use REST commands and SSE events. SSE resumes by daemon `event_id`, uses heartbeats, bounded buffers and explicit snapshot reconciliation. Do not implement WebSocket in MVP.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| REST plus SSE | Selected: simple command semantics and one-way event delivery. |
| WebSocket | Rejected: multiplexing/reconnect/backpressure complexity exceeds MVP need. |
| Polling only | Rejected: poorer fault/progress responsiveness. |

## Consequences

### Positive

- Standard HTTP authorization and proxy support.
- Event loss is handled explicitly through snapshots.

### Negative

- Client cannot use a single duplex connection.

## Implementation constraints

- Browser event overflow must emit `snapshot.required`, never silently drop.
- SSE IDs are daemon `event_id`, not protocol `request_id`.

## Validation and revisit triggers

- Test resume, heartbeat, overflow and slow-client isolation.
- Revisit when a justified duplex interaction cannot use REST/SSE.

## Links

- [API events](../api-and-events.md); [PI-API-002](../backlog.md#pi-api-002--implement-resumable-bounded-sse-event-stream).

# ADR-0003: Python cs71d and single serial ownership

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

`cs71_protocol` already defines strict Python v1/v2, CRC and recovery behavior. Multiple serial owners or async callback I/O would undermine correlation and recovery.

## Decision

Implement Python `cs71d` as the sole serial owner. A dedicated worker thread exclusively uses `ProtocolClient`; all other components exchange typed queued intents/snapshots.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| `cs71d` worker using `cs71_protocol` | Selected: reuses tested protocol boundary and serializes control. |
| SvelteKit serial access | Rejected: violates trust and restart boundaries. |
| Multiple daemon workers | Rejected: permits competing protocol/session ownership. |

## Consequences

### Positive

- One correlation/recovery authority.
- Browser/web failure cannot directly alter serial ownership.

### Negative

- Worker queue and snapshot publication need careful backpressure design.

## Implementation constraints

- No second code path opens the configured device.
- POSIX/Linux DTR is not represented as qualified by this decision.

## Validation and revisit triggers

- Static/runtime tests prove exclusive ownership and bounded queues.
- Revisit only if controller protocol supports explicit safe multiplexing.

## Links

- [Host README](../../../host/README.md); [Runtime](../runtime-and-domain.md); [PI-DAEMON-001](../backlog.md#pi-daemon-001--implement-sole-serial-worker-and-admission-queues).

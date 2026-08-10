# ADR-0004: Internal HTTP/JSON over Unix domain socket

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

The two local processes need observable typed communication without exposing machine control to the LAN. OpenAPI tooling favors HTTP semantics.

## Decision

Use versioned HTTP/JSON over `/run/cs71/cs71d.sock` with filesystem permission-based service access. Do not bind daemon control to TCP.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| HTTP/JSON Unix domain socket | Selected: local-only, debuggable and OpenAPI-compatible. |
| Loopback TCP | Rejected: unnecessarily expands exposure. |
| In-process binding | Rejected: conflicts with two-process boundary. |

## Consequences

### Positive

- Clear resource/error semantics and narrow network attack surface.
- SvelteKit can use standard generated clients.

### Negative

- Socket lifecycle/mode/group need service coordination.

## Implementation constraints

- Socket permits only the designated web service identity.
- Caddy never proxies the daemon API.

## Validation and revisit triggers

- Test no TCP listener, socket modes and service connectivity.
- Revisit for a demonstrated remote trusted-control requirement with new security design.

## Links

- [API](../api-and-events.md); [Security](../security-and-safety.md); [PI-API-001](../backlog.md#pi-api-001--serve-openapi-backed-unix-socket-rest-api).

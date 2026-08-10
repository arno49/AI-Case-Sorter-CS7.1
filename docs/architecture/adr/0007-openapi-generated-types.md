# ADR-0007: OpenAPI contract and generated TypeScript types

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

Separate Python and TypeScript implementations need an auditable API contract and drift prevention.

## Decision

Maintain an OpenAPI source contract for daemon `/v1` and generate TypeScript types/client from it. Contract compatibility is a CI gate.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| OpenAPI plus generated types | Selected: schema, documentation and client stay aligned. |
| Handwritten duplicated types | Rejected: drift likely at service boundary. |
| Protocol frames in web | Rejected: daemon API and firmware protocol are distinct. |

## Consequences

### Positive

- Versioned contract review and typed BFF calls.
- Repeatable compatibility testing.

### Negative

- Generation tool/version must be maintained.

## Implementation constraints

- API major version is independent from firmware protocol version.
- Generated output must not erase daemon/protocol identifier distinction.

## Validation and revisit triggers

- CI fails on schema/client drift and breaking v1 change.
- Revisit for an equivalent proven contract tool only.

## Links

- [API](../api-and-events.md); [PI-ARCH-002](../backlog.md#pi-arch-002--define-v1-daemon-api-contract-baseline); [PI-API-001](../backlog.md#pi-api-001--serve-openapi-backed-unix-socket-rest-api).

# ADR-0011: Local authentication/RBAC/session model

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

The private-LAN appliance still needs attributable, least-privilege local operation and protection from browser threats without cloud dependency.

## Decision

Use local accounts, Argon2id password hashes, opaque server-side sessions, CSRF protections and Viewer/Operator/Administrator RBAC enforced by SvelteKit server code.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Local sessions and RBAC | Selected: works offline and supports attribution. |
| Anonymous LAN control | Rejected: unacceptable command authority. |
| Cloud identity dependency | Rejected: conflicts with offline appliance goal. |

## Consequences

### Positive

- Offline user control with auditable roles.
- Browser receives no daemon credential.

### Negative

- Requires provisioning, password recovery and session operations.

## Implementation constraints

- Stop is available to every authenticated role, not anonymous users.
- No default password; bootstrap is one-time and expiry-bound.

## Validation and revisit triggers

- Test all RBAC, CSRF, rotation/revocation and rate-limit behavior.
- Revisit for a local enterprise identity provider integration with equivalent offline/safety behavior.

## Links

- [Security](../security-and-safety.md); [PI-WEB-001](../backlog.md#pi-web-001--implement-local-authentication-and-sessions); [PI-WEB-002](../backlog.md#pi-web-002--enforce-server-side-rbac-and-csrf).

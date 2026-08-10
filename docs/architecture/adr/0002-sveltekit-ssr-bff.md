# ADR-0002: SvelteKit SSR as web/BFF

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

Operators need a local, accessible web UI with server-side authorization. Browsers must not receive daemon or serial access.

## Decision

Use SvelteKit SSR on Node.js as the only browser-facing BFF/UI. It owns sessions, RBAC, CSRF, SSR pages and the browser SSE bridge.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| SvelteKit SSR | Selected: combines SSR and controlled server actions for a small appliance. |
| Browser-to-daemon SPA | Rejected: exposes privileged control boundary. |
| Desktop/native client | Rejected: expands installation/support scope. |

## Consequences

### Positive

- One HTTPS origin and server-side enforcement.
- Browser reconnects do not own machine retries.

### Negative

- Node service and generated client require maintenance.

## Implementation constraints

- SvelteKit reaches only the Unix domain socket daemon API.
- A 202 command acceptance is never rendered as completion.

## Validation and revisit triggers

- Test SSR/RBAC/CSRF and Node restart during active daemon work.
- Revisit for validated non-browser operator-client requirements.

## Links

- [Runtime](../runtime-and-domain.md); [PI-WEB](../backlog.md#epic-pi-web--sveltekit-platform-and-authentication); [PI-UI](../backlog.md#epic-pi-ui--operator-ui).

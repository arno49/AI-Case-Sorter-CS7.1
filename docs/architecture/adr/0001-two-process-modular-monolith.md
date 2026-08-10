# ADR-0001: Two-process modular monolith

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

The appliance needs a strong hardware boundary without distributed-system operational cost. Existing direction separates UI/BFF concerns from Python protocol control.

## Decision

Deploy one modular appliance as two processes: Node.js SvelteKit and Python `cs71d`. Keep their internal modules cohesive; do not create additional network services for MVP.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Two-process modular monolith | Selected: isolates serial authority while retaining single-host operations. |
| Single Node process | Rejected: would duplicate/replace the existing Python protocol boundary. |
| Microservices | Rejected: adds failure and deployment complexity without MVP benefit. |

## Consequences

### Positive

- Serial failure and web restart are isolated.
- Deployment remains understandable on one Pi.

### Negative

- Requires explicit internal contract and two service lifecycles.

## Implementation constraints

- Node restart must not interrupt `cs71d` serial operations.
- Cross-process communication uses the accepted local transport only.

## Validation and revisit triggers

- Prove web restart isolation in integration/HIL tests.
- Revisit only for demonstrated multi-machine or isolation requirements.

## Links

- [System context](../system-context.md); [PI-DAEMON](../backlog.md#epic-pi-daemon--cs71d-session-and-serial-ownership); [PI-BFF](../backlog.md#epic-pi-bff--bff-integration).

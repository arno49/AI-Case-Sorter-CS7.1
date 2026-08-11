# CS7.1 Raspberry Pi 5 Architecture

**Status:** Proposed implementation architecture; decisions listed below are accepted. This set is the canonical architecture for the Raspberry Pi 5 control appliance. It supersedes detailed design material in [RASPBERRY_PI_WEB_ARCHITECTURE.md](../../RASPBERRY_PI_WEB_ARCHITECTURE.md), which is now an executive summary. Firmware wire behavior remains canonical in [ArduinoCode/PROTOCOL_V2.md](../../ArduinoCode/PROTOCOL_V2.md); its delivery gates remain in [ArduinoCode/PROTOCOL_V2_PLAN.md](../../ArduinoCode/PROTOCOL_V2_PLAN.md).

## Document map

| Document | Primary audience | Canonical subject |
| --- | --- | --- |
| [vision-and-scope.md](vision-and-scope.md) | product, safety, delivery | outcome, scope, targets, assumptions and gates |
| [system-context.md](system-context.md) | all contributors | boundaries, containers, dependencies and failures |
| [runtime-and-domain.md](runtime-and-domain.md) | daemon and web engineers | state, scheduling, recovery and event semantics |
| [api-and-events.md](api-and-events.md) | daemon and BFF engineers | internal HTTP/JSON and SSE contract rules |
| [data-and-persistence.md](data-and-persistence.md) | service and operations engineers | database ownership and recovery |
| [security-and-safety.md](security-and-safety.md) | security, safety, operators | threats, access, invariants and safety boundary |
| [deployment-and-operations.md](deployment-and-operations.md) | release and operations engineers | Pi deployment and runbooks |
| [testing-and-quality.md](testing-and-quality.md) | QA and CI engineers | evidence, tests and gates |
| [roadmap.md](roadmap.md) | delivery leads | milestones and dependency order |
| [backlog.md](backlog.md) | implementers | PR-sized work and objective acceptance criteria |
| [traceability.md](traceability.md) | reviewers | requirements-to-evidence mapping |
| [adr/README.md](adr/README.md) | decision makers | accepted decision index and ADR process |

The executable daemon API source is
[`appliance/contracts/cs71d-v1.openapi.json`](../../appliance/contracts/cs71d-v1.openapi.json);
architecture prose explains its semantics but does not replace it.

## Status legend

| Label | Meaning |
| --- | --- |
| **Accepted** | A binding architectural decision for this appliance. |
| **Target** | A measurable desired level, not a measured result. |
| **Gate** | Work or evidence required before a stated use is allowed. |
| **NOT_EXECUTED** | A result deliberately not performed; it is not a pass. |
| **UNCERTAIN** | Hardware/session state cannot be trusted; dependent motion is blocked. |

## Principles

1. `cs71d` is the sole serial-device owner and calls `cs71_protocol`; the browser reaches only SvelteKit.
2. Safety is fail-closed: lack of trusted correlation, journal durability, or recovery verification is never success.
3. Daemon API versioning and IDs are separate from the Arduino protocol and its `request_id`.
4. Node/SvelteKit restart must not interrupt a serial operation already owned by `cs71d`.
5. A software stop is not an E-stop. Linux DTR behavior is NOT_EXECUTED until its dedicated hardware gate passes.
6. Simulator evidence proves simulator-supported behavior only and never closes a hardware gate.

## Decision and delivery indexes

The accepted decisions are [ADR-0001](adr/0001-two-process-modular-monolith.md) through [ADR-0012](adr/0012-versioned-operations-events-idempotency.md). Delivery work is dependency-ordered in [backlog.md](backlog.md); the milestone view is [roadmap.md](roadmap.md). The requirement-to-test route is [traceability.md](traceability.md).

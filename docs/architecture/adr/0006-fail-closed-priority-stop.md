# ADR-0006: Fail-closed machine state and priority stop

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

Unsafe correlation, transport loss, recovery failure or journal failure cannot support a truthful success claim. Operators also need a software stop that does not wait behind normal work.

## Decision

Use `UNCERTAIN` fail-closed semantics and a priority software-stop lane. Require trusted firmware terminals for success; mark affected operations uncertain on ambiguity. Software stop is explicitly not an E-stop.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Fail closed plus priority stop | Selected: preserves evidence and preempts routine work. |
| Best-effort success after timeout | Rejected: can misrepresent machine state. |
| UI-only cancellation | Rejected: does not reach firmware. |

## Consequences

### Positive

- Ambiguity is visible and inhibits dependent motion.
- Stop does not wait for normal queue admission.

### Negative

- Recovery workflow can require operator intervention.

## Implementation constraints

- Stop uses the existing universal protocol handling and still requires outcome evidence.
- Journal failure blocks new state-changing work; it cannot be ignored.

## Validation and revisit triggers

- Inject timeout, disconnect, terminal mismatch and journal error tests.
- Revisit if firmware offers a stronger verified safety primitive, never to call software stop E-stop.

## Links

- [Runtime](../runtime-and-domain.md); [Safety](../security-and-safety.md); [PI-DOMAIN-002](../backlog.md#pi-domain-002--implement-journal-failure-and-priority-stop-semantics).

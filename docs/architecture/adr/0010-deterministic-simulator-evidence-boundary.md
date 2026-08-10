# ADR-0010: Deterministic simulator with strict hardware evidence boundary

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

No-hardware development needs fast repeatability, but simulated serial behavior cannot establish electrical, mechanical or safety facts.

## Decision

Build a deterministic, seedable simulator and label its evidence. Simulator results never satisfy DTR, physical motion, stop, USB electrical or production hardware gates.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Deterministic simulator plus HIL boundary | Selected: fast test feedback without false physical claims. |
| Hardware-only testing | Rejected: slow and insufficient for exhaustive faults. |
| Simulator treated as HIL substitute | Rejected: cannot observe physical behavior. |

## Consequences

### Positive

- Repeatable faults, timing and stress scenarios.
- Clear qualification evidence language.

### Negative

- Simulator fidelity must be maintained and still needs real parity checks.

## Implementation constraints

- Production profile rejects simulator selection.
- Tests use injected time, seeds and fixture versioning.

## Validation and revisit triggers

- Replay normative fixtures and compare real serial transcripts in HIL.
- Revisit model scope when firmware behavior changes; never relax evidence boundary.

## Links

- [Testing](../testing-and-quality.md); [PI-SIM](../backlog.md#epic-pi-sim--deterministic-simulator); [PI-HIL](../backlog.md#epic-pi-hil--hardware-qualification).

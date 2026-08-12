# ADR-0013: Vision classifier as a third appliance service with configurable routing and hybrid autonomy

**Status:** Proposed<br>
**Date:** 2026-08-12

## Context

Headstamp classification today runs entirely outside this repository, on a
separate Windows PC ("AI Sorter", `ArduinoCode/PROTOCOL.md`'s minimum
supported version `1.1.46`) wired directly to the controller. The goal of
this decision is to bring that capability onto the appliance itself —
Arduino Uno plus Raspberry Pi 5, no separate Windows PC — as a new,
explicitly out-of-MVP capability (`vision-and-scope.md` lists "classifier
implementation" under MVP exclusions; this ADR is what would lift that
exclusion for a later phase).

Facts this decision is built on, gathered before writing it rather than
assumed:

- Cases reaching this stage are already sorted by caliber with a physical
  sieve upstream of the appliance. What remains to classify is
  **manufacturer headstamp**, and a single batch may contain several dozen
  distinct manufacturers.
- The physical machine has **8 output chutes by default**
  (`SORTER_SLOT_COUNT` in `ArduinoCode/CS71_Arduino/CS71_Arduino.ino`), with
  8- or 10-slot printable attachments; the daemon API accepts up to
  `MAX_API_SLOT = 63` (`appliance/daemon/src/cs71d/api.py`), but that is a
  protocol ceiling, not a statement about what is mechanically buildable.
  Several dozen manufacturer classes do not fit a handful of physical
  chutes — every design here has to say what happens to the classes that
  do not get their own chute.
- Cases may or may not carry a live/unfired primer. This is not "one more
  classification label": misjudging it is a physical-risk category this
  project already treats with extreme conservatism everywhere else (E-stop
  independence, DTR gate). It gets the same treatment here.
- No trained model and no labeled dataset exist yet, for headstamps or for
  primer presence. Whatever this ADR proposes has to work starting from
  nothing.
- `cs71d`'s command API validates every `Actor.role` against a fixed set,
  `API_ROLES = {viewer, operator, administrator}`
  (`appliance/daemon/src/cs71d/api.py`) — there is no concept today of a
  command attributed to anything other than a human web session. Every
  durable operation, RBAC capability and audit row currently assumes a
  person made the decision.
- The v2 "feed" lifecycle is gated `NOT_EXECUTED`
  (`FEED_LIFECYCLE_GATE`, `appliance/daemon/src/cs71d/adapters.py`) — the
  same evidence-status posture as the Linux DTR gate. There is currently no
  software-driven "a case is now in position" signal a vision component
  could trigger a capture from; that gate closing is a precondition for a
  fully autonomous capture→classify→sort loop, not something this ADR can
  route around.
- The camera hardware already specified in
  `3DModels/Classifier/CameraV2/Camera Assembly V2.pdf` is a plain USB VGA
  UVC module (640×480, driverless, works with the OS's default camera app on
  Windows today) plus a 5V LED ring and a 12V fan. On Linux this is a
  standard V4L2 device — no CSI ribbon, no `libcamera` dependency.

## Decision

Add a **third appliance service**, provisionally named `cs71-vision`, that
owns the camera and inference pipeline — the same single-responsibility
split `cs71d` (sole serial owner) and `cs71-web` (browser BFF) already use,
not folded into either. `cs71d` keeps owning the controller and the safety
model; `cs71-vision` never touches the serial device directly and only ever
asks `cs71d` to act, the same way `cs71-web` does today.

**Data comes from ordinary operation, not a separate labeling effort.**
Phase 0 ships no classifier at all: while an operator sorts by hand as they
do today, `cs71-vision` records the camera frame alongside the slot the
operator actually chose (correlated by `operation_id`), building a labeled
dataset for free. No model is trained, and nothing is classified
automatically, until there is real data to train on.

**Routing is operator-selectable configuration, not one hardcoded
strategy.** Several dozen manufacturer classes into 8–10 chutes has more
than one defensible mapping, and different runs may want different ones.
`cs71-vision` exposes a small set of routing profiles, selected through the
web UI the same way other daemon configuration is already changed
(`OperationDomain.configure`/`ConfigurationValues`):

- a fixed class→chute map with one overflow chute for everything else,
- dynamic per-batch assignment (the first N distinct classes seen in this
  run claim a chute; the UI must then show the operator which chute means
  which class *for this run*, since that mapping is not stable across
  runs),
- a two-pass mode: a first pass sorts into coarse groups, a second pass (run
  as its own job against one group's output) sorts that group more finely.

**Autonomy is confidence-gated per class, starting conservative.** Below a
configured confidence threshold, a case is held for the operator to confirm
through the web UI rather than sorted automatically. The threshold is
expected to start high (mostly manual, matching Phase 0/1's thin dataset)
and only relax as the model is retrained on more data, including the
corrections an operator makes to low-confidence predictions.

**Primer presence is never decided by confidence alone.** A case flagged,
or ambiguous, for a live/unfired primer always requires operator
confirmation, independent of the routing/manufacturer confidence threshold
and independent of how the autonomy setting above is configured. This
mirrors the DTR gate and E-stop posture already in this codebase: software
does not get to declare a physical-risk judgment safe on its own say-so.

**A command attributed to `cs71-vision` needs a new, narrowly-scoped actor
kind in `cs71d`'s contract**, not a reuse of an existing human role. It is
authenticated by its own service credential the same way `cs71-web` is
today — never a browser session — and is restricted to submitting `sort`
operations only; it can never reach `machine.recover`, `config.write` or
`users.manage`. This is a daemon and OpenAPI contract change
(`API_ROLES`, the `Actor` shape, `appliance/contracts/cs71d-v1.openapi.json`),
not a web-only addition, and every such command still lands in the existing
durable journal and audit trail with this new attribution visible as what
it is — a system decision, not a person's.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Third service `cs71-vision`, camera/inference isolated from `cs71d`/`cs71-web` | Selected: keeps `cs71d`'s single-serial-owner discipline and test model intact; matches how this codebase already separates concerns. |
| Fold vision into `cs71d` | Rejected: `cs71d` is deliberately the *sole serial owner and nothing else* — adding a camera/ML workload there breaks that boundary and its safety-review story. |
| Fold vision into the SvelteKit BFF | Rejected: Node.js is a poor fit for camera capture/inference, and the BFF has no device access today by design. |
| Keep classification external on a Windows PC, Pi only orchestrates motion | Rejected: this is the thing the user explicitly wants removed. |
| One hardcoded routing strategy | Rejected: several dozen classes into 8–10 chutes has no single right answer across different batches/use cases; made configurable instead. |
| Reuse an existing human role (e.g. `operator`) for autonomous commands | Rejected: collapses "a person decided" and "a model decided" into the same audit trail entry, which this project's attribution model exists specifically to avoid. |

## Consequences

### Positive

- No separate Windows PC; the whole system is Uno + Pi 5.
- Data collection starts on day one of Phase 0 with zero extra operator
  effort, at essentially no cost or risk (nothing is classified or acted on
  automatically yet).
- Routing strategy stays a configuration choice, not a rewrite, as real
  usage reveals which mapping actually works for a given batch shape.
- The primer-presence rule and the new actor kind keep the existing
  "physical risk is never a software claim" discipline intact rather than
  quietly eroding it for the sake of automation.

### Negative

- A new actor kind is a daemon safety-model and contract change, reviewed
  with the same weight as `cs71d`'s existing safety primitives — not a
  small addition.
- Fully autonomous end-to-end operation (capture → classify → sort with no
  operator in the loop) cannot be reached until the feed lifecycle gate
  closes; until then, feed remains a manual/operator-driven step regardless
  of how good the classifier is.
- Dynamic per-batch routing requires new UI (a live chute↔class legend)
  that does not exist anywhere in this codebase today.
- Model quality is bounded by whatever Phase 0 self-labeled data actually
  captures; a biased or thin operator-driven sample yields a biased model,
  and that has to be watched for, not assumed away.

## Implementation constraints

- `cs71-vision` never opens the serial device or bypasses `cs71d`'s
  admission path; every sort it triggers goes through the same
  idempotency/generation/durability rules any other command does.
- The new actor kind is added to `API_ROLES` and the OpenAPI contract as an
  additive, versioned change, following the same discipline PI-UI-002's
  `/v1/system` addition already established (additive, not a breaking
  change to `FROZEN_*` contract guards).
- Primer-presence confirmation cannot be bypassed by raising the
  manufacturer-classification confidence threshold; it is a separate gate,
  enforced independently.
- Camera integration targets plain V4L2 (matching the confirmed USB VGA UVC
  hardware in `3DModels/Classifier/CameraV2`); no `libcamera`/CSI dependency
  is assumed unless a different camera is chosen later.
- Until the feed lifecycle gate closes, `cs71-vision` cannot assume a
  software signal for "a case is now in position"; a capture trigger has to
  be designed around that absence (e.g. frame-stability detection) rather
  than waiting on a gate this ADR does not control.

## Validation and revisit triggers

- Phase 0 exit: a labeled dataset exists from real operator-driven sorting,
  with per-class counts high enough to attempt a first model.
- Phase 1 exit: a first model runs read-only (suggestion only, operator
  always confirms); measured accuracy per class is recorded before any
  autonomy is considered.
- Before relaxing the autonomy threshold for any class: recorded accuracy
  and a false-autonomous-sort rate for that class, not a general "it seems
  to work."
- Before ever reconsidering the "primer presence always confirms" rule:
  dedicated physical trial evidence, reviewed the same way DTR/HIL evidence
  is — this is explicitly not expected to relax on software evidence alone.
- Revisit this ADR if the feed lifecycle gate closes (PI-DOMAIN-003/PI-HIL
  territory), since that changes what "capture trigger" can rely on.

## Links

- [Vision and scope](../vision-and-scope.md) (MVP exclusion this ADR
  proposes lifting for a later phase); [System context](../system-context.md);
  [Security and safety](../security-and-safety.md); [Runtime and domain](../runtime-and-domain.md).
- [ADR-0003](0003-python-cs71d-single-serial-owner.md) (why `cs71d` stays
  the sole serial owner and gains no new responsibilities here);
  [ADR-0006](0006-fail-closed-priority-stop.md) (the fail-closed posture
  this decision extends to a new actor kind);
  [ADR-0011](0011-local-auth-rbac-sessions.md) (the RBAC/attribution model
  a machine actor must not silently reuse).
- No backlog epic yet: this ADR is the alignment step requested before one
  is written.

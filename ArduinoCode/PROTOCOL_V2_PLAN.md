# CS7.1 protocol v2 implementation and rollout plan

## 1. Objective

Implement the opt-in protocol defined in
[`PROTOCOL_V2.md`](PROTOCOL_V2.md) without changing default v1 behavior or
breaking the existing Windows application.

Delivery is split into reviewable feature branches. Each firmware branch starts
from the updated `main`, passes its own acceptance gate, and merges before the
next dependent firmware branch begins.

V2 is not complete when frames parse successfully. Completion requires:

- byte-for-byte v1 regression protection;
- correlated v2 command lifecycles;
- compatibility switcher;
- Arduino Uno flash/SRAM review;
- representative hardware qualification;
- successful run with the existing Windows application;
- documented rollback.

## 2. Non-negotiable constraints

- Reset always starts in v1.
- V2 is activated only by the documented handshake.
- Protocol mode and CRC state are never persisted.
- Existing v1 command strings and responses remain unchanged.
- The v1 `getconfig` JSON schema remains unchanged.
- Shared command handlers drive both protocol versions.
- No dynamic allocation or Arduino `String` is introduced.
- The exact ID-less `stop` remains available in both modes.
- Hardware validation is a release gate, not an automated-test substitute.
- Firmware remains directly buildable for Arduino Uno.

## 3. Delivery model

| Track | Owner profile | Scope |
| --- | --- | --- |
| Firmware | Embedded engineer | Parser, session, responses, state integration, CRC |
| Host tooling | Application/tooling engineer | Host library and `cs71-protocol` CLI |
| Verification | QA/embedded engineer | Golden traces, native tests, serial mock, HIL |
| Compatibility | Windows application operator | Existing application regression |
| Release | Maintainer | Versioning, artifacts, rollout, rollback |

Relative effort:

- **S**: isolated change with limited state interaction;
- **M**: several modules or a new state transition;
- **L**: physical lifecycle or cross-component integration.

No calendar estimate should be committed until bench hardware, Windows
application access, and the host-tool implementation language are assigned.

## 4. Dependency overview

```text
V2-00 specification approval
  |
V2-01 v1 golden baseline
  |
V2-02 shared protocol foundation
  |
V2-03 negotiation and session mode
  |
V2-04 v2 envelope and response lifecycle
  +-------------------------+
  |                         |
V2-05 observability      HOST-01 host library
  |                         |
V2-06 configuration      HOST-02 switcher CLI
  |
V2-07A priority stop/cancellation
  |
V2-07B faults/state events
  |
V2-08 home/direct sort
  |
V2-08H physical-completion smoke
  |
V2-09 feed/queue/diagnostics
  |
V2-10 CRC
  +------------+------------+
               |
V2-11 software integration gate
               |
V2-12 hardware qualification
               |
V2-13 Windows compatibility
               |
V2-14 staged release
```

Host work may proceed against specification fixtures after the envelope is
frozen in V2-04. It is validated against real firmware incrementally as the
matching commands land, with full parity required at V2-11.
Firmware branches V2-01 through V2-10 remain sequential to avoid competing
changes in the Uno dispatcher and machine state.

## 5. Execution stages and AIC allocation

AIC values are planning limits, not guaranteed prices. Actual consumption
depends on model selection, failed builds, review iterations, and subagent use.
Check `/usage` and `/limits` after every merged PR.

| Stage | Included tasks | Primary result | Planning AIC |
| --- | --- | --- | ---: |
| 1 — Baseline and feasibility | V2-00..V2-02 | Golden v1 tests, `uno_v2`, resource decision | 200 |
| 2 — Safe protocol shell | V2-03..V2-05 | Negotiation, envelope, IDs, status and capabilities | 180 |
| 3 — Configuration and safety | V2-06, V2-07A, V2-07B | Shared setters, stop, cancellation, faults and events | 180 |
| 4 — Physical operations | V2-08, V2-08H, V2-09 | Physical completion, queue, feed and diagnostics | 250 |
| 5 — Integrity and tooling | V2-10, HOST-01, HOST-02 | CRC, host library and compatibility CLI | 170 |
| 6 — Qualification and release | V2-11..V2-14 | Candidate, HIL, Windows qualification and rollout | 70 |
| Reserve | Cross-stage corrections | Build failures, AVR resource reduction and fixes | 150 |
| **Total** |  |  | **1200** |

The allocation is deliberately asymmetric: physical state integration receives
the largest allowance, while hardware execution itself consumes human bench
time rather than large model context.

### Stage 1 — Baseline and feasibility

Goal: protect v1 automatically and prove Uno feasibility before implementing v2
behavior.

Outputs:

- approved protocol and ADR;
- captured and automated v1 golden responses;
- native-testable dispatch foundation;
- `uno` and `uno_v2` CI environments;
- initial flash/SRAM budget.

Gate:

- **Go** only if wire-facing v1 behavior is covered and projected v2 resource
  use is acceptable.
- **Stop or reduce scope** if dispatch cannot be tested natively or Uno headroom
  is inadequate.

### Stage 2 — Safe protocol shell

Goal: make v2 observable and testable without controlling physical operations.

Outputs:

- exact discovery, activation, and return to v1;
- optional request IDs and `@0`;
- deterministic terminal responses;
- `protocolversion`, `capabilities`, `status`, and `queue`.

Gate:

- **Go** when old firmware, new firmware in v1, and new firmware in v2 are
  distinguishable without ambiguity.
- **Stop** on any v1 golden difference, uncorrelated terminal response, or
  unsafe reset transition.

### Stage 3 — Configuration and safety

Goal: connect shared configuration and establish cancellation/fault semantics
before exposing movement.

Outputs:

- shared setters and streamed v2 `getconfig`;
- correlated and out-of-band stop;
- deterministic cancellation ordering;
- state/fault events and latched fault status.

Gate:

- **Go** when stop cannot produce stale `done` and v1 setters/errors remain
  byte-identical.
- **Stop** if v1/v2 update different state or fault recovery is ambiguous.

### Stage 4 — Physical operations

Goal: add physical completion incrementally and verify home/sort on hardware
before extending the lifecycle to feed and diagnostics.

Outputs:

- correlated homing and direct-sort completion;
- mandatory intermediate bench smoke;
- feed phases and material-wait liveness;
- observable two-position queue;
- correlated diagnostics.

Gate:

- **Go to feed work only after V2-08H confirms physical `done` timing.**
- **Stop** on early completion, queue/physical mismatch, stale response, or v1
  motion regression.

### Stage 5 — Integrity and tooling

Goal: finish wire integrity and provide the supported compatibility workflow.

Outputs:

- optional CRC-16;
- independently tested host protocol library;
- `cs71-protocol` detection, switching, and legacy preparation;
- packaged tooling for approved host platforms.

Gate:

- **Go** when old/new firmware detection, uncertain CRC recovery, DTR handling,
  and exclusive port ownership pass.
- **Stop** if tooling can accidentally activate v2 or hand the Windows app an
  uncertain session.

### Stage 6 — Qualification and release

Goal: produce a qualified and reversible opt-in release.

Outputs:

- integrated candidate and resource report;
- full HIL evidence;
- unmodified Windows application sign-off;
- controlled beta;
- published artifacts and rehearsed rollback.

Gate:

- **Release only after both hardware and Windows gates pass.**
- Any unexplained misroute, fault, timing difference, or v1 trace change returns
  the candidate to the responsible earlier stage.

### AIC operating rules

1. Use a fresh focused session for each task/PR.
2. Read only the relevant plan and protocol sections.
3. Prefer targeted tests and `uno_v2` builds over repeated full audits.
4. Use one independent review after implementation rather than overlapping
   agents during exploration.
5. Record AIC usage after every PR.
6. If a stage exceeds its allocation by 25%, pause and re-estimate before using
   the reserve.
7. Preserve at least 75 AIC until V2-11 integration is complete.

## 6. Task backlog

### V2-00 — Approve protocol and implementation ADR

| Field | Value |
| --- | --- |
| Branch | `docs/v2-protocol-approval` |
| Effort | S |
| Depends on | None |
| Changes hardware behavior | No |

Tasks:

1. Review `PROTOCOL_V2.md` with firmware, host, and hardware stakeholders.
2. Freeze the v2 grammar, negotiation responses, error codes, CRC algorithm,
   queue terminology, and ID-less behavior.
3. Decide the implementation language and packaging target for
   `cs71-protocol`.
4. Record who owns bench hardware and Windows compatibility sign-off.
5. Mark unresolved items explicitly; do not silently decide them in code.

Acceptance:

- [x] No open ambiguity affects framing, terminal responses, stop, or fallback.
- [x] The retained command vocabulary and terminal examples pass the readability
      gate in `PROTOCOL_V2.md`.
- [x] Python 3/`pyserial` is the host-tool target and `@arno49` coordinates
      protocol, bench, and Windows sign-off.
- [x] The specification and implementation ADR were approved before firmware
      behavior changes.

### V2-01 — Capture and automate the v1 golden baseline

| Field | Value |
| --- | --- |
| Branch | `test/v2-v1-golden-baseline` |
| Effort | M |
| Depends on | V2-00 |
| Changes hardware behavior | No |

Tasks:

1. Capture byte-exact v1 transcripts for startup, `ping`, `version`,
   `getconfig`, every setter class, invalid input, busy handling, stop, homing,
   direct sort, numeric feed, `xf:`, diagnostics, and feed overtravel.
2. Add native tests for existing helpers and parsers that can run without
   Arduino APIs.
3. Add a deterministic serial mock/replay fixture for host tests.
4. Record the current Uno flash and static SRAM baseline.
5. Record hardware-only traces separately without claiming automation.

Acceptance:

- [ ] Authoritative v1 wire transcripts are captured as fixtures even though
      `.ino` dispatch is not yet native-testable.
- [ ] Existing native-testable helpers and parsers have automated coverage.
- [ ] Fixtures preserve leading spaces, mixed case, spelling mistakes, and
      exact JSON shape.
- [ ] The baseline includes the two-position queue.
- [ ] Resource measurements are stored for later comparison.
- [ ] `pio run -e uno` and `pio test -e native` pass.

Automated byte-for-byte wire regression becomes mandatory in V2-02 after the
wire-facing dispatcher/output is extracted from the `.ino`.

### V2-02 — Extract the shared protocol foundation

| Field | Value |
| --- | --- |
| Branch | `refactor/v2-protocol-foundation` |
| Effort | L |
| Depends on | V2-01 |
| Changes hardware behavior | No |

Tasks:

1. Introduce a small `ProtocolMode` model with v1 as the reset default.
2. Extract v1 command interpretation and response emission from the `.ino` into
   modules compiled by the native environment.
3. Add a response sink/interface that can emit either unchanged v1 lines or
   structured v2 lines.
4. Reuse existing strict numeric, boolean, and geometry validators.
5. Keep the existing 41-byte pending buffer v1-only.
6. Add compile-time `PROTOCOL_V2_ENABLED` gating for development builds.
7. Add `env:uno_v2` with `PROTOCOL_V2_ENABLED=1` while keeping `env:uno` as the
   default compatibility build.
8. Update CI to build both Uno environments on every relevant PR.
9. Convert the V2-01 wire fixtures into byte-exact native golden tests.
10. Perform an early v2 resource spike and stop for scope review if projected
   Uno headroom is unacceptable.

Acceptance:

- [ ] V2 remains disabled and unreachable in the default build for this PR.
- [ ] Golden v1 traces remain byte-identical.
- [ ] A change to any covered v1 response causes a native test failure.
- [ ] V1 and future v2 parser/session/dispatch modules compile under
      `env:native`.
- [ ] No second motor-control path is created.
- [ ] Response selection uses a mode branch or another measured low-overhead
      mechanism; virtual dispatch is not introduced without explicit budget.
- [ ] No dynamic allocation or `String` appears.
- [ ] Both `pio run -e uno` and `pio run -e uno_v2` pass.
- [ ] Resource delta is reported from `uno_v2`, not from a build that compiles
      v2 out.

### V2-03 — Add discovery, activation, and session fallback

| Field | Value |
| --- | --- |
| Branch | `feature/v2-session-negotiation` |
| Effort | M |
| Depends on | V2-02 |
| Changes hardware behavior | No |

Tasks:

1. Implement `protocol:2?`, `protocol:2`, and v2 `protocol:1`.
2. Intercept negotiation before the v1 pending-command branch.
3. Reject activation while busy without retaining the request.
4. Switch parser only after the complete terminal LF is transmitted.
5. Clear partial frames, IDs, event sequence, and CRC at session boundaries.
6. Prove reset always restores pristine v1.

Acceptance:

- [ ] Old firmware behavior is represented by the discovery `ok` fixture.
- [ ] Exact responses are required; `ok` never activates v2.
- [ ] Lost activation response is recoverable only through stop/reset.
- [ ] Existing Windows traffic cannot enter v2 accidentally.
- [ ] V1 golden and native session-transition tests pass.

### V2-04 — Implement the readable envelope and request lifecycle

| Field | Value |
| --- | --- |
| Branch | `feature/v2-readable-envelope` |
| Effort | L |
| Depends on | V2-03 |
| Changes hardware behavior | No |

Tasks:

1. Parse optional `@id ` while preserving the following v1 payload.
2. Enforce 40-byte v1 and 64-byte total v2 limits.
3. Reserve `@0` for ID-less request responses.
4. Track one active state-changing ID and one immediate read-only request.
5. Implement `accepted`, `progress:`, `data:`, `done`, `error:`, and `reject:`.
6. Guarantee one terminal response for each trusted request.
7. Implement event sequence and modular wrap.
8. Reject unknown v2 commands instead of returning legacy success-shaped `ok`.

Acceptance:

- [ ] Commands can be entered manually with or without IDs.
- [ ] Duplicate/invalid IDs cannot terminate another request.
- [ ] Partial, concatenated, overlong, NUL, malformed, and unknown frames are
      covered.
- [ ] V2 does not inherit the v1 pending state-changing queue.
- [ ] Golden v1 traces remain unchanged.

### V2-05 — Add read-only observability

| Field | Value |
| --- | --- |
| Branch | `feature/v2-observability` |
| Effort | M |
| Depends on | V2-04 |
| Changes hardware behavior | No |

Tasks:

1. Implement `protocolversion`.
2. Implement streamed `capabilities`.
3. Implement streamed `status`.
4. Implement `queue`.
5. Expose read-only accessors from existing machine state instead of
   duplicating state.
6. Allow these queries during one active physical request when they have an
   available ID.

Acceptance:

- [ ] Every required field from the specification is emitted.
- [ ] Every response line remains within 64 bytes.
- [ ] Event gaps can be repaired with a complete `status` snapshot.
- [ ] Queue fields reflect `qPos1`/`qPos2` semantics.
- [ ] Unknown future fields can be ignored by the host fixture.

### V2-06 — Route configuration through shared handlers

| Field | Value |
| --- | --- |
| Branch | `feature/v2-configuration` |
| Effort | M |
| Depends on | V2-05 |
| Changes hardware behavior | Runtime settings only |

Tasks:

1. Route existing `name:value` setters through shared validation/actions.
2. Return effective values and `config_generation` in v2.
3. Stream v2 `getconfig` fields, including `SlotCount`.
4. Keep the v1 JSON object unchanged and continue omitting `SlotCount`.
5. Preserve setter ordering constraints for `sortsteps:` and `slotcount:`.

Acceptance:

- [ ] Every v1 setter has an equivalent v2 trace using the same payload.
- [ ] Invalid values leave configuration unchanged.
- [ ] V1 and v2 update the same underlying variables.
- [ ] Optional PWM capability and configuration remain consistent.
- [ ] Reconnect/reset is documented and tested as volatile configuration loss.

### V2-07A — Integrate priority stop and cancellation

| Field | Value |
| --- | --- |
| Branch | `feature/v2-priority-stop` |
| Effort | M |
| Depends on | V2-06 |
| Changes hardware behavior | Exposes existing stop behavior |

Tasks:

1. Implement correlated `@id stop`.
2. Preserve exact ID-less `stop` before mode/framing validation.
3. Correlate cancellation of the active request.
4. Ensure stop invalidates both axes and forces the completion signal low.
5. Suppress every stale terminal response after cancellation.

Acceptance:

- [ ] Stop ordering matches the specification.
- [ ] ID-less stop works with v1, v2, malformed correlation state, and later
      CRC-enabled sessions.
- [ ] No stale `done` appears after stop.
- [ ] Existing stop latency is not regressed in native timing/state tests.

### V2-07B — Add fault mapping and state events

| Field | Value |
| --- | --- |
| Branch | `feature/v2-fault-state-events` |
| Effort | M |
| Depends on | V2-07A |
| Changes hardware behavior | Exposes existing fault/state behavior |

Tasks:

1. Emit deterministic `state:` events from existing state transitions.
2. Map feed overtravel to stable code 3001.
3. Emit one request-correlated error and one global `fault:` event.
4. Expose latched fault state through `status`.
5. Clear faults only through documented recovery/reset.

Acceptance:

- [ ] A machine fault emits one event and one correlated terminal error.
- [ ] Protocol rejects never masquerade as machine faults.
- [ ] Fault state and affected homing validity agree.
- [ ] Event sequence and status resynchronization tests pass.
- [ ] V1 feed-overtravel output remains byte-identical.

### V2-08 — Add physical completion for homing and direct sort

| Field | Value |
| --- | --- |
| Branch | `feature/v2-home-sort-lifecycle` |
| Effort | L |
| Depends on | V2-07B |
| Changes hardware behavior | No intended motion change |

Tasks:

1. Add v2 lifecycle callbacks to `homefeeder`, `homesorter`, and `sortto:`.
2. Implement `homeall` as serialized feeder then sorter recovery.
3. Emit phase progress from existing cooperative states.
4. Emit `done` only after physical completion.
5. Preserve v1 immediate `ok` behavior for the same handlers.
6. Preserve `sortto:` queue synchronization.

Acceptance:

- [ ] V1 still acknowledges immediately and emits no new completion line.
- [ ] V2 emits `accepted` before motion and exactly one terminal response.
- [ ] `done` cannot precede final step/homing offset completion.
- [ ] Stop/fault cancels each phase correctly.
- [ ] Native state tests cover success, cancellation, and failure.

### V2-08H — Bench-smoke physical completion

| Field | Value |
| --- | --- |
| Branch | No feature branch; test the V2-08 `uno_v2` artifact |
| Effort | S |
| Depends on | V2-08 |
| Requires | Representative CS7.1 hardware |

Tasks:

1. Confirm `homefeeder`, `homesorter`, `homeall`, and `sortto:` emit `done`
   after observed physical completion.
2. Stop each operation during motion and verify cancellation.
3. Compare v1 motion and immediate acknowledgement on the same artifact.
4. Record serial and physical observations before extending the lifecycle to
   feed/diagnostics.

Acceptance:

- [ ] No early physical `done` is observed.
- [ ] V1 behavior remains unchanged on hardware.
- [ ] Cancellation leaves the documented homing validity.
- [ ] Any discrepancy is fixed before V2-09 begins.

### V2-09 — Add feed pipeline and diagnostic lifecycles

| Field | Value |
| --- | --- |
| Branch | `feature/v2-feed-pipeline` |
| Effort | L |
| Depends on | V2-08H |
| Changes hardware behavior | No intended feed/sort change |

Tasks:

1. Support numeric, `feed:`, `xf:`, and `forcefeed:` payloads.
2. Report `drop_slot` and `queued_slot`.
3. Convert waiting, movement, settling, AirDrop, and notification phases into
   request-owned progress.
4. Preserve at-most-once-per-second wait liveness.
5. Add correlated `test:` and `sorttest:` progress.
6. Delay v2 diagnostic terminal `done` until physical return completes.

Acceptance:

- [ ] Queue priming and steady-state traces match physical semantics.
- [ ] V1 continues emitting its exact historical lines.
- [ ] Forced feed bypasses only material readiness.
- [ ] Progress does not create extra terminal responses.
- [ ] Stop and overtravel terminate feed/diagnostics without deadlock.

### V2-10 — Add optional CRC-16

| Field | Value |
| --- | --- |
| Branch | `feature/v2-crc16` |
| Effort | M |
| Depends on | V2-09 |
| Changes hardware behavior | No |

Tasks:

1. Implement CRC-16/CCITT-FALSE without dynamic allocation.
2. Add `crc:on` and `crc:off` transitions.
3. Protect responses/events only after the defined transition boundary.
4. Emit correlated-safe `reject:1002:bad_crc`.
5. Keep exact ID-less `stop` CRC-exempt.
6. Clear CRC on v1 transition and reset.

Acceptance:

- [ ] Published known-answer vectors pass.
- [ ] Bad/missing CRC never executes a request.
- [ ] Enable, disable, `protocol:1`, and reset boundaries are deterministic.
- [ ] CRC-disabled v2 behavior is unchanged.
- [ ] Uno resource delta is measured.

### HOST-01 — Implement the protocol host library

| Field | Value |
| --- | --- |
| Branch | `feature/v2-host-library` |
| Effort | L |
| Depends on | V2-04 |
| May run in parallel | V2-05 through V2-10 |

Tasks:

1. Implement LF/CRLF framing with leading-space preservation for v1.
2. Implement v1 and v2 response classifiers.
3. Implement discovery and exact negotiation.
4. Implement request-ID allocation and terminal-response tracking.
5. Implement event sequence validation and status resynchronization.
6. Implement CRC independently from firmware code.
7. Use the serial mock and golden transcripts from V2-01.

Acceptance:

- [ ] The library talks to both old and proposed firmware fixtures.
- [ ] Unknown fields are tolerated; unknown terminal forms fail closed.
- [ ] Activation/CRC uncertainty causes stop/reset rather than guessing.
- [ ] The application API exposes typed status, fault, queue, and completion.
- [ ] Tests run without physical hardware.
- [ ] Parallel work is validated against specification fixtures; parity with
      real firmware remains an explicit V2-11 gate.

### HOST-02 — Implement `cs71-protocol`

| Field | Value |
| --- | --- |
| Branch | `feature/v2-compatibility-cli` |
| Effort | M |
| Depends on | HOST-01 and V2-05 |

Tasks:

1. Implement `detect`, `enter-v2`, `leave-v2`, `prepare-legacy`, and
   `run-legacy`.
2. Add human-readable and `--json` output.
3. Add exclusive serial-port ownership.
4. Control DTR explicitly where the platform permits.
5. Implement stable documented exit codes.
6. Package the tool for the selected supported host platforms.

Acceptance:

- [ ] Detection distinguishes old firmware `ok` from v2 availability.
- [ ] `prepare-legacy` leaves the controller in verified v1 before handoff.
- [ ] Port locks are released before launching the Windows app.
- [ ] Platform limitations around automatic DTR are surfaced, not hidden.
- [ ] Old/new firmware, crash, timeout, and port-contention fixtures pass.

### V2-11 — Software integration and resource gate

| Field | Value |
| --- | --- |
| Branch | `release/v2-software-candidate` |
| Effort | M |
| Depends on | V2-10, HOST-02 |
| Changes hardware behavior | Candidate integration only |

Tasks:

1. Enable compiled v2 support while retaining runtime v1 default.
2. Run the complete native suite and Uno build.
3. Compare `uno_v2` flash/SRAM against V2-01 and every recorded intermediate
   delta; review remaining headroom.
4. Run host library and switcher tests against a firmware serial harness.
5. Perform long-running parser/state tests including event wrap and reconnect.
6. Assign the candidate firmware version.

Acceptance:

- [ ] All automated suites pass from a clean checkout.
- [ ] No unexplained v1 golden difference exists.
- [ ] Resource use is approved for Uno; otherwise scope is reduced.
- [ ] Every v2 request path has one terminal response.
- [ ] Candidate artifacts and checksums are retained.

### V2-12 — Hardware-in-the-loop qualification

| Field | Value |
| --- | --- |
| Branch | No firmware feature branch; test candidate artifact |
| Effort | L |
| Depends on | V2-11 |
| Requires | Representative CS7.1 hardware |

Tasks:

1. Verify startup, discovery, activation, return to v1, and reset.
2. Validate feeder/sorter direction, homing, offsets, and physical completion.
3. Trace stop during every motion/timer phase.
4. Verify proximity wait/settling and forced feed.
5. Verify queue fields through priming, steady state, and flush procedure.
6. Induce feed overtravel and complete recovery.
7. Measure AirDrop signal and camera-safe `done`.
8. Run repeated cycles and reconnect tests.

Acceptance:

- [ ] Wire responses correspond to observed physical completion.
- [ ] Stop and overtravel leave the machine in the documented state.
- [ ] Queue reporting matches actual drop destinations.
- [ ] No v1 hardware behavior regression is found.
- [ ] Evidence includes timestamped traces and pass/fail results.

### V2-13 — Existing Windows application qualification

| Field | Value |
| --- | --- |
| Branch | No feature branch; qualification record |
| Effort | M |
| Depends on | V2-12 |
| Requires | Supported Windows app and representative workflow |

Tasks:

1. Reset candidate firmware and confirm it starts in v1.
2. Connect the unmodified supported Windows application.
3. Exercise configuration, homing, training/camera workflow as applicable,
   normal sorting, forced feed, diagnostics, stop, and recovery.
4. Compare serial traces with the V2-01 baseline.
5. Run `cs71-protocol prepare-legacy` and repeat startup.

Acceptance:

- [ ] The Windows application requires no configuration or code change.
- [ ] It never observes v2-only lines.
- [ ] Its complete representative sorting workflow succeeds.
- [ ] Any timing difference is explained and approved.
- [ ] Compatibility is explicitly signed off.

### V2-14 — Staged release and rollback

| Field | Value |
| --- | --- |
| Branch | `release/protocol-v2` |
| Effort | M |
| Depends on | V2-13 |

Tasks:

1. Publish firmware, source, switcher, protocol documents, checksums, and known
   limitations.
2. Keep v1 as reset/default mode.
3. Roll out first to maintainers, then one controlled beta machine, then
   additional opt-in users.
4. Monitor faults, negotiation failures, resets, misroutes, and support cases.
5. Retain the last qualified v1 firmware artifact.
6. Document runtime and firmware rollback.

Acceptance:

- [ ] Release notes state that v2 is opt-in and v1 remains supported.
- [ ] Users can return to v1 with `protocol:1` or reset.
- [ ] The switcher can prepare legacy operation.
- [ ] The previous qualified firmware can be reflashed.
- [ ] Promotion occurs only after the observation gate in Section 8.

## 7. Pull request policy

Every PR must include:

- task ID and specification sections implemented;
- explicit statement of v1-visible behavior;
- native test additions;
- `pio run -e uno`, `pio run -e uno_v2` where available, and
  `pio test -e native` result;
- flash/SRAM delta from `pio run -e uno_v2` for firmware PRs after V2-02;
- protocol transcript for changed wire behavior;
- hardware validation status: performed, not required, or still pending;
- rollback description.

A firmware PR must not combine:

- parser/session infrastructure with motor behavior;
- CRC with initial framing;
- host tooling with firmware implementation;
- automated completion with hardware sign-off.

CI must build both `uno` and `uno_v2` after V2-02 so resource or compile
failures cannot remain hidden behind the default-off feature flag.

## 8. Rollout gates

| Gate | Entry requirement | Exit requirement |
| --- | --- | --- |
| G0 — Design approved | V2-00 complete | Wire contract frozen |
| G1 — Software alpha | V2-11 complete | Automated suites and resource gate pass |
| G2 — Bench alpha | G1 | HIL qualification passes |
| G3 — Legacy qualified | G2 | Existing Windows workflow passes |
| G4 — Controlled beta | G3 | One opt-in machine runs without unexplained fault/misroute |
| G5 — General opt-in | G4 | Maintainer approves broader release |

No elapsed-time value is prescribed for G4 until expected cycle volume and
failure tolerance are agreed. The gate should be defined by observed cycles and
fault-free behavior rather than calendar time alone.

## 9. Rollback

Runtime rollback:

1. stop active work;
2. send `protocol:1` if the v2 session is healthy;
3. otherwise use ID-less `stop` and reset;
4. wait for `Ready` and the v1 `ping` barrier;
5. reapply volatile v1 configuration.

Release rollback:

1. stop and power-isolate as required;
2. flash the retained qualified v1 firmware artifact;
3. verify its checksum and version;
4. repeat startup/homing smoke tests;
5. restore the validated Windows application profile.

A failed v2 rollout does not require a Windows application rollback because the
existing application never opts into v2.

## 10. Definition of done

Protocol v2 is delivered only when:

- [ ] V2-00 through V2-14, including V2-07A, V2-07B, and V2-08H, are complete.
- [ ] V1 golden behavior remains unchanged.
- [ ] Uno resource use is approved.
- [ ] Host library and switcher are released.
- [ ] Hardware and Windows compatibility are signed off.
- [ ] Controlled beta meets its cycle/fault gate.
- [ ] Rollback has been rehearsed, not merely documented.

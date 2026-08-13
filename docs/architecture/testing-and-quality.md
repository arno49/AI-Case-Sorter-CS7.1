# Testing and Quality

## Evidence policy

A simulator validates only its declared contract. It may satisfy unit, integration, UI and controlled stress cases, but **never** DTR, USB electrical behavior, physical motion/completion, stop latency, sensor, reboot-under-motion or hardware release gates. Reports label backend, fixture/version, device identity (for HIL), test data and result type. A test does not turn an unmeasured target into a result.

## Test pyramid and matrix

| Layer | Primary evidence | Required examples |
| --- | --- | --- |
| Unit/property | pure Python/TypeScript | domain transitions, idempotency fingerprinting, generation monotonicity, parser bounds, RBAC/CSRF helpers |
| Protocol parity | existing `cs71_protocol` fixtures plus transcripts | v1 discovery, v2 activation, CRC boundaries, request/event tracking, exact terminals and recovery |
| Daemon integration | deterministic simulator | serial ownership, queue bounds, stop preemption, journal errors, reconnect, SSE overflow |
| API/contract | OpenAPI/client tests | schema/error mapping, compatibility, headers, SSE resume/snapshot-required |
| BFF/browser | SSR and browser tests | auth/session/CSRF, role routes, no optimistic completion, a11y keyboard/screen reader checks |
| Pi resource/deploy | controlled Pi profile | service restart isolation, memory/CPU/disk thresholds, udev/install/backup/restore |
| HIL | selected physical rig | DTR, real serial parity, homing/sort/stop/fault/USB/power recovery |

## Simulator and protocol contract

The deterministic simulator implements only documented firmware behavior needed by tests, uses injected clock/scheduling (no test sleeps), seedable scenarios and golden transcript replay. It models startup, v1/v2 negotiation, optional CRC, accepted/progress/terminal responses, events, configurable timing, faults, disconnects, malformed frames, event gaps and stop. It is selected by explicit development/test configuration and is refused by production profile validation. Simulator changes require comparison against normative protocol fixtures; they do not reinterpret ambiguous firmware behavior.

Protocol parity tests import/use the real `cs71_protocol` public boundary, including `ProtocolClient`, typed models and scripted transport. Daemon tests must prove no alternate serial-opening/parsing path exists. Fuzz/property tests cover line framing bounds, unexpected response/event order, sequence gaps, request-id wrapping, duplicate idempotency requests, stale generations and scheduler invariants. Stress tests force slow SSE consumers, normal-lane saturation, repeated reconnect and concurrent BFF calls while asserting bounded memory and no duplicate operation execution.

The versioned Raspberry Pi 5 software profile measures NFR-01 and NFR-02
without a controller. It records Pi model, OS/kernel, CPU governor, process
versions, load generator, sample count and percentile method. Priority-stop
admission must be reported within 250 ms, excluding firmware and physical stop
time. At least 99% of internal snapshot reads must complete within 100 ms.
Failure blocks software qualification; simulator or desktop measurements cannot
replace this Pi profile. `appliance/daemon/scripts/measure_nfr.py`
(PI-SWQ-001) implements load profile v1 and runs in CI on every push, but a
GitHub-hosted runner is never the approved rig, so its own report always
stamps `evidence_status: NOT_EXECUTED`; re-running the identical script on
the approved Raspberry Pi 5 is what actually closes this gate, the same
"NOT_EXECUTED is not performed, not failed" posture SAF-07 already uses for
the Linux DTR gate.

## Browser, accessibility and security

Browser tests run server-rendered flows with JavaScript disabled where supported and enabled for SSE behavior. They cover login/logout/session expiration, CSRF rejection, each RBAC denial/allowance, stale UI command rejection, stop visibility, `UNCERTAIN` prominence, event overflow resync and daemon/web restart isolation. Accessibility checks include semantic forms, keyboard-only stop/recovery flows, focus management, contrast and announced state/fault changes; manual assistive-technology review is a pilot entry criterion. Security tests include auth/session fixation, CSRF, request-size/rate limits, socket non-exposure, secret redaction and dependency audit review.

## HIL cases and evidence

HIL procedures have fixture revision, technician, safeguards, controller/adapter identity, firmware build, Pi image, instrument settings, raw results and pass/fail threshold. Required cases include: device identity; real v1/v2/CRC parity; DTR open/reset measurements; home/sort trusted terminals versus observed behavior; priority software-stop response and physical behavior; fault injection; USB loss; daemon restart; web restart during daemon operation; power/reboot recovery; storage/journal fault response; and soak under defined load. Software stop is tested as software behavior and never certified as E-stop.

## CI and release gates

Every PR runs formatting/lint/type checks already present for touched workspace, unit tests, protocol fixtures, contract compatibility, generated-client drift, relevant browser tests and docs/link checks. Mainline additionally runs deterministic integration/stress and dependency/license/security review jobs available in the repository/toolchain. Hardware evidence is stored/referenced separately and gated manually: no CI simulator success can mark it passed. Releases require clean build provenance, migration/rollback and backup/restore evidence, all software gates, approved HIL evidence, closed DTR gate and runbook drill.

Flakes are quarantined only with issue, owner, reproducible evidence and expiry; quarantined tests do not count as passed release evidence. Test data is synthetic or approved fixture data, versioned where practical, minimal, and contains no secrets or operator credentials.

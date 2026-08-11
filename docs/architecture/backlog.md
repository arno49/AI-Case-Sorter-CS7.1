# Delivery Backlog

Tasks are dependency-ordered and deliberately PR-sized. Size is a rough planning label: S (≤2 engineer-days), M (≤4), L (≤8). No XL task is permitted. “Hardware required” means the task's acceptance criteria cannot be closed without physical evidence.

## Epic PI-ARCH — Architecture and contracts

**Outcome:** implementers have one approved vocabulary, contract boundary and safety baseline. **Dependencies:** none. **Out of scope:** application implementation and hardware qualification. **Epic exit criteria:** accepted ADRs, traceability and versioned contract conventions are reviewed; all terms use `cs71d`, SvelteKit, Unix domain socket, SSE, `operation_id`, daemon `event_id`, protocol `request_id`, snapshot generation and `UNCERTAIN` consistently.

### PI-ARCH-001 — Publish canonical architecture set

**Goal:** establish the canonical architecture document set. **Implementation notes:** cross-link existing firmware/host canonical sources; avoid re-specifying wire grammar. **Dependencies:** none. **Hardware required:** No. **Size:** S.

- All required architecture documents and ADR index/template exist under `docs/architecture/`.
- The root architecture document identifies canonicality, status legend and document audiences.
- The executive summary links to detailed canonical documents without conflicting normative rules.
- Local relative links resolve and document terms are used consistently.

### PI-ARCH-002 — Define v1 daemon API contract baseline

**Goal:** freeze initial resource, error, ID and compatibility rules. **Implementation notes:** author OpenAPI source in the implementation PR; preserve daemon/protocol ID separation. **Dependencies:** PI-ARCH-001. **Hardware required:** No. **Size:** M.

**Status:** Implemented in `appliance/contracts/cs71d-v1.openapi.json`; executable baseline and invariant tests run in CI.

- `/v1` resources, required headers and representative schemas match [api-and-events.md](api-and-events.md).
- Contract distinguishes `operation_id`, daemon `event_id`, protocol `request_id` and snapshot generation.
- Additive versus breaking compatibility rules have executable contract tests planned.
- No browser-addressable or TCP daemon API endpoint is specified.

## Epic PI-FOUNDATION — Repository foundation and CI

**Outcome:** daemon and web work can build/test reproducibly without a controller. **Dependencies:** PI-ARCH-002. **Out of scope:** production deployment and UI features. **Epic exit criteria:** clean checkout runs configured quality checks, generated contract drift is detected and simulator selection is explicit.

### PI-FOUNDATION-001 — Create daemon and web workspaces

**Goal:** add isolated Python `cs71d` and Node/SvelteKit workspace scaffolds. **Implementation notes:** consume `cs71_protocol` as a library; do not modify its public wire behavior. **Dependencies:** PI-ARCH-002. **Hardware required:** No. **Size:** M.

**Status:** Implemented under `appliance/daemon` and `appliance/web`; both
workspaces have reproducible package manifests and safe no-device development
defaults.

- A clean checkout builds both workspaces using documented existing package commands.
- `cs71d` imports `cs71_protocol` rather than copying parser/client code.
- Development configuration uses no real serial device by default.
- Production configuration validation rejects simulator backend and arbitrary device paths.

### PI-FOUNDATION-002 — Add quality and contract CI gates

**Goal:** automate scoped formatting, lint, type, test and generated-client checks. **Implementation notes:** use existing ecosystem tools; cache is optional optimization only. **Dependencies:** PI-FOUNDATION-001. **Hardware required:** No. **Size:** M.

**Status:** Implemented with separate daemon/web CI jobs, generated OpenAPI
TypeScript drift detection, revision/dependency metadata, audit artifacts, and
explicit `SOFTWARE_SIMULATOR_ONLY` evidence labels.

- CI fails on daemon/web test, type or generated contract drift in changed scopes.
- Build metadata records source revision and dependency lock/manifests where supported.
- CI labels simulator evidence separately from any hardware evidence.
- Dependency/license/security reports are produced or explicitly marked unavailable without masking test failures.

## Epic PI-SIM — Deterministic simulator

**Outcome:** repeatable no-hardware controller scenarios exercise daemon integration. **Dependencies:** PI-FOUNDATION-001. **Out of scope:** hardware validation or claims of physical parity. **Epic exit criteria:** deterministic fixtures cover supported happy/fault/recovery paths and production cannot select simulator accidentally.

### PI-SIM-001 — Implement deterministic protocol simulator

**Goal:** provide an injected-clock CS7.1 simulator around the protocol boundary. **Implementation notes:** model documented v1/v2 negotiation, CRC, events and terminals; never sleep to advance test time. **Dependencies:** PI-FOUNDATION-001. **Hardware required:** No. **Size:** L.

**Status:** Implemented under `appliance/daemon/src/cs71d/simulator` with an
explicit manual clock, seeded scenarios, real `cs71_protocol` integration, and
conspicuous `SIMULATOR_ONLY` identity.

- Given the same seed/scenario, emitted bytes, events and terminals are identical.
- Scenarios cover startup, legacy discovery, v2 activation, optional CRC and status/capabilities/queue responses.
- Time advances through explicit test control rather than wall-clock sleeps.
- Simulator uses a conspicuous backend identity in logs and test reports.

### PI-SIM-002 — Add fault and transcript scenario fixtures

**Goal:** model adverse behavior and replay normative fixtures. **Implementation notes:** use existing golden/protocol fixtures as source evidence; keep simulator deltas reviewable. **Dependencies:** PI-SIM-001. **Hardware required:** No. **Size:** M.

**Status:** Simulator-side fixtures are implemented with named deterministic
fault/disconnect/malformed/timeout/event-gap/terminal-mismatch scenarios and
strict replay of the existing v1/v2 golden files through `ProtocolClient`.
The terminal-mismatch fixture proves fail-closed recovery at the protocol
boundary. Its daemon operation-result assertion is closed by PI-DOMAIN-001:
`test_an_unverified_terminal_makes_the_operation_uncertain` shows the injected
mismatch resolving the durable operation as `UNCERTAIN`, never successful.

- Fixtures cover stop, fault, disconnect, malformed frame, timeout and event-gap behavior.
- Existing v1 and v2 golden transcripts replay through the intended `cs71_protocol` boundary.
- A scenario can inject a terminal mismatch and prove daemon result becomes `UNCERTAIN` or failed.
- Test reports state that simulator results do not satisfy HIL/DTR gates.

## Epic PI-DAEMON — `cs71d` session and serial ownership

**Outcome:** one fail-closed daemon owns all controller I/O. **Dependencies:** PI-SIM-002. **Out of scope:** browser auth/UI and Linux DTR qualification. **Epic exit criteria:** static/runtime tests demonstrate one serial owner, bounded queue, verified transitions and no success after unsafe transport events.

### PI-DAEMON-001 — Implement sole serial worker and admission queues

**Goal:** isolate `ProtocolClient` in one dedicated serial worker. **Implementation notes:** API/domain threads enqueue typed intents only; use bounded normal and priority-stop lanes. **Dependencies:** PI-SIM-002. **Hardware required:** No. **Size:** L.

**Status:** Implemented as `cs71d.SerialWorker`. One dedicated thread
constructs the transport and `ProtocolClient` and performs every read and
write; callers submit closed typed intents and receive futures. A static test
confines the `ProtocolClient` import to `serial_worker.py`, and a concurrent
start/submit test proves a second serial open cannot occur. Preemption uses the
host prerequisite's deadline-preserving interrupt polling and trusted
same-owner out-of-band stop, so no cross-thread serial access is required.
A failed stop marks the affected result `UNCERTAIN` rather than stopped or
successful.

- Only serial-worker code can construct/use `ProtocolClient` or configured serial transport.
- Normal queue saturation returns a defined rejection without unbounded allocation.
- Exactly one state-changing operation is dispatched at a time.
- Priority stop is admitted independently of normal-lane saturation.
- A test proves BFF/API concurrency cannot create a second serial open.

### PI-DAEMON-002 — Implement session state and conservative reconnect

**Goal:** publish connection states and recover without command replay. **Implementation notes:** delegate v1/v2/CRC recovery to `cs71_protocol`; enforce unqualified DTR policy. **Dependencies:** PI-DAEMON-001. **Hardware required:** No. **Size:** L.

**Status:** Implemented as `cs71d.SessionState` plus reconnect handling in
`SerialWorker`. Connection state is published separately from the worker
thread's lifecycle, because a healthy thread can own an untrustworthy session.
In-session recovery is delegated entirely to `cs71_protocol`, which already
runs stop/reset/verify for an unsafe exchange and reports the outcome through
`RecoveryError.recovered`; the worker only decides whether to re-activate v2 or
escalate to a full reconnect on a fresh transport. Real-port opening is gated
in `cs71d.device`, which refuses POSIX opens while the DTR gate is
`NOT_EXECUTED`.

- Connection transitions include `DISCONNECTED`, `VERIFYING_V1`, `READY`, `RECOVERING` and `UNCERTAIN` with generation changes.
- Timeout, malformed frame, CRC fault or device loss marks affected operation non-successful.
- Recovery never automatically replays an incomplete state-changing command.
- POSIX/Linux real-port open is blocked or explicitly development-only until DTR gate configuration is satisfied.
- Simulator tests prove successful verified recovery and unsuccessful recovery remain distinguishable.

## Epic PI-DOMAIN — Scheduler, snapshots and journal

**Outcome:** durable machine operations have correct concurrency and uncertainty semantics. **Dependencies:** PI-DAEMON-002. **Out of scope:** SQL access by SvelteKit or firmware redesign. **Epic exit criteria:** operation lifecycle, idempotency, stale generation, typed home/sort/feed dispatch, priority stop and journal-failure behavior are covered by tests.

### PI-DOMAIN-001 — Implement operations, idempotency and snapshot generation

**Goal:** admit and track durable operation lifecycle. **Implementation notes:** canonical request fingerprint includes action/body/actor policy; protocol `request_id` is diagnostic only. **Dependencies:** PI-DAEMON-002. **Hardware required:** No. **Size:** L.

**Status:** Implemented as `cs71d.operations` (identity, lifecycle, canonical
fingerprint), `cs71d.journal` (`machine.db` with forward-only checksummed
migrations), `cs71d.machine` (the machine-wide snapshot generation) and
`cs71d.domain` (admission, dispatch and terminal outcome). Admission evaluates
the idempotency key, the observed generation and readiness while holding the
machine view still, and makes the operation durable before anything is
enqueued, so a stale or duplicate request never reaches the controller. The
generation belongs to the machine, not the session: connection confidence is
folded into the same counter that operation transitions advance. `SUCCEEDED`
without a trusted firmware terminal is refused by the operation model and,
independently, by a database trigger, and a transmitted command without a
trusted terminal resolves as `UNCERTAIN` rather than failed. Protocol
`request_id` is stored only as diagnostic session-scoped metadata. Capability
and advertised-range validation remain with PI-DOMAIN-003.

- Each admitted command has a UUID `operation_id`, finite deadline and durable lifecycle transition record.
- Same idempotency key and equivalent request returns the original operation; a differing request conflicts.
- Stale snapshot generation rejects before serial enqueue.
- Every material state transition increases snapshot generation monotonically.
- `SUCCEEDED` requires a trusted correlated firmware terminal in tests.

### PI-DOMAIN-002 — Implement journal failure and priority-stop semantics

**Goal:** make durability and stop preemption fail closed. **Implementation notes:** persist intent/transition before reporting durable state; route stop through protocol library universal-stop behavior. **Dependencies:** PI-DOMAIN-001. **Hardware required:** No. **Size:** M.

**Status:** Implemented in `cs71d.machine` and `cs71d.domain`. A refused
journal write latches the machine view as undurable with a `LATCHED` fault; it
stops admitting work and rejects new motion with `JOURNAL_UNAVAILABLE`. It does
not self-clear, because durability loss needs operator or service
intervention. The dispatch gate means a lifecycle write that cannot be recorded
stops the command before transmission, and a terminal that cannot be recorded
leaves the operation non-successful rather than being claimed in memory.
Priority stop is a durable attributable operation admitted through
`OperationDomain.stop`; it skips the readiness check ordinary motion must
pass, accepts `*` for the observed generation, and is routed through the
protocol library's exact universal stop. A stop is refused when it cannot be
recorded: an unattributable software stop is a claim this daemon does not make,
and the physical E-stop is the independent safety device.

- Injected journal write failure rejects new motion with `JOURNAL_UNAVAILABLE` and changes readiness/fault status.
- Journal failure cannot yield an unrecorded successful operation in tests.
- Stop bypasses queued normal work and creates an attributable stop operation.
- Missing trusted `stopped` terminal marks active/affected work `UNCERTAIN`, never stopped-successful.

### PI-DOMAIN-003 — Implement typed home, sort and feed operation adapters

**Goal:** map the MVP machine intents to typed `cs71_protocol` calls without exposing raw commands. **Implementation notes:** capability and firmware-gate checks precede dispatch; feed remains unavailable in production until V2-09 and its hardware evidence pass. **Dependencies:** PI-DOMAIN-002. **Hardware required:** No for simulator implementation; production enablement requires the existing firmware/HIL gates. **Size:** M.

- Home accepts only feeder, sorter or both and records the trusted terminal fields.
- Sort validates the advertised slot range and known sorter position before serial enqueue.
- Feed validates advertised capability and readiness; an unqualified firmware build returns `NOT_READY` or `UNSUPPORTED` without serial I/O.
- Simulator tests cover accepted, progress, trusted completion, fault, cancellation and `UNCERTAIN` for all three operation types.
- No API or BFF input can provide an arbitrary protocol payload.

## Epic PI-API — Internal API and SSE

**Outcome:** SvelteKit has a versioned, testable local contract. **Dependencies:** PI-DOMAIN-003. **Out of scope:** public LAN daemon access and WebSockets. **Epic exit criteria:** OpenAPI/client, headers/errors, socket-only service and bounded resumable SSE pass integration tests.

### PI-API-001 — Serve OpenAPI-backed Unix-socket REST API

**Goal:** expose daemon resources over internal HTTP/JSON. **Implementation notes:** bind only Unix domain socket with service authentication; generate TypeScript client from source contract. **Dependencies:** PI-DOMAIN-003. **Hardware required:** No. **Size:** L.

- Daemon starts with no TCP listener and socket mode/group excludes browser users.
- Snapshot, connect, recover, stop, home, sort, feed, operation and health resources conform to generated OpenAPI tests.
- State-changing endpoints require idempotency, generation and finite-deadline headers.
- Error codes map to documented HTTP status without exposing raw secrets or protocol internals.
- Generated TypeScript changes fail CI when contract and client diverge.

### PI-API-002 — Implement resumable bounded SSE event stream

**Goal:** publish daemon events without serial-worker backpressure. **Implementation notes:** retain daemon event ring; keep daemon `event_id` distinct from protocol `request_id`. **Dependencies:** PI-API-001. **Hardware required:** No. **Size:** M.

- Every emitted event has monotonic daemon `event_id`, UTC timestamp, type and snapshot generation.
- `Last-Event-ID` resumes retained events in order.
- A stale cursor or subscriber overflow emits `snapshot.required` and does not silently omit changes.
- Heartbeats occur under idle conditions and do not change machine generation.
- Slow/closed clients cannot block priority stop or serial worker progress.

## Epic PI-WEB — SvelteKit platform and authentication

**Outcome:** a secure SSR BFF safely represents local operators. **Dependencies:** PI-FOUNDATION-002. **Out of scope:** daemon serial logic and public API exposure. **Epic exit criteria:** local provisioning, login/session/RBAC/CSRF and web database ownership are tested server-side.

### PI-WEB-001 — Implement local authentication and sessions

**Goal:** secure local user lifecycle and server sessions. **Implementation notes:** Argon2id hashes, opaque server-side sessions, one-time bootstrap admin. **Dependencies:** PI-FOUNDATION-002. **Hardware required:** No. **Size:** L.

- Fresh installation has no usable default password and requires expiry-bound bootstrap provisioning.
- Password storage uses Argon2id; plaintext passwords/tokens never appear in database/log tests.
- Cookies are Secure/HttpOnly/SameSite according to production origin policy and sessions rotate on login.
- Logout, expiry, disable and password-change revoke existing sessions.
- `web.db` access is exclusive to web service identity.

### PI-WEB-002 — Enforce server-side RBAC and CSRF

**Goal:** protect all BFF state-changing routes. **Implementation notes:** roles map to documented matrix; UI visibility is supplementary only. **Dependencies:** PI-WEB-001. **Hardware required:** No. **Size:** M.

- Tests exercise each allowed and denied role/action pair, including Viewer software stop.
- State-changing forms/actions reject missing/invalid CSRF and invalid Origin policy.
- Server handlers, not client code, perform authorization before daemon call.
- Login and command endpoints apply documented size/rate controls.

## Epic PI-BFF — BFF integration

**Outcome:** SvelteKit translates authorized intent to local daemon operations and safely bridges events. **Dependencies:** PI-API-002, PI-WEB-002. **Out of scope:** direct browser daemon calls and client-side operation completion. **Epic exit criteria:** BFF is socket-only, preserves semantic headers and survives web restarts without serial impact.

### PI-BFF-001 — Implement generated daemon client and command translation

**Goal:** call `cs71d` from SvelteKit server actions. **Implementation notes:** pass authenticated actor attribution, idempotency, generation and deadline; never forward browser raw commands. **Dependencies:** PI-API-002, PI-WEB-002. **Hardware required:** No. **Size:** M.

- Browser requests cannot select daemon URL, serial path or arbitrary protocol command.
- BFF supplies required command headers and maps daemon errors to safe user responses.
- Accepted response displays `operation_id`/pending state, not completion.
- BFF audit entries correlate actor, request and `operation_id` without shared database writes.

### PI-BFF-002 — Implement browser SSE bridge and restart isolation

**Goal:** maintain browser view from daemon events/snapshots. **Implementation notes:** BFF fans out/reconnects; it does not own/retry machine operations. **Dependencies:** PI-BFF-001. **Hardware required:** No. **Size:** M.

- Browser reconnect after event overflow fetches snapshot before presenting incremental updates.
- BFF restart during a simulated daemon operation neither cancels nor duplicates it.
- Browser event disconnect does not block daemon event production or serial worker.
- UI-facing events preserve daemon `event_id`, `operation_id` and generation semantics.

## Epic PI-UI — Operator UI

**Outcome:** operators can safely complete MVP workflows with clear uncertainty. **Dependencies:** PI-BFF-002. **Out of scope:** raw protocol console, classifier workflow and native mobile app. **Epic exit criteria:** MVP pages pass browser/a11y flows and never make premature completion or E-stop claims.

### PI-UI-001 — Build dashboard, manual and operations views

**Goal:** deliver status, command and history screens. **Implementation notes:** pages are SSR first and capability-driven; expose snapshot generation to command forms. **Dependencies:** PI-BFF-002. **Hardware required:** No. **Size:** L.

- Dashboard shows connection, homing, active operation, faults and snapshot generation from daemon snapshot.
- Manual controls validate capability/slot and submit idempotency/generation-protected intent.
- Feed controls are capability-driven and remain unavailable with an explicit reason until the firmware feed lifecycle gate is qualified.
- Operation history shows accepted, progress and terminal states with trusted-terminal status.
- UI never labels HTTP acceptance as machine completion.
- Keyboard-only automated flow can find and activate software stop.

### PI-UI-002 — Build fault, recovery and system views

**Goal:** make unsafe state and maintenance evidence actionable. **Implementation notes:** recovery/reset requires administrator confirmation; no raw serial access. **Dependencies:** PI-UI-001. **Hardware required:** No. **Size:** M.

- `UNCERTAIN` is visually and semantically more prominent than ordinary status.
- Dependent motion controls are disabled when readiness/homing/capabilities are unknown.
- Recovery/reset prompt requires explicit administrator confirmation and records outcome.
- System view shows versions, storage/journal health and DTR-gate status without claiming it passed.

## Epic PI-OPS — Deployment and operations

**Outcome:** appliance installation is repeatable and observable. **Dependencies:** PI-API-001, PI-WEB-002. **Out of scope:** containers and Internet-hosted service. **Epic exit criteria:** clean Pi install, restricted services/socket/device mapping, backup/restore and rollback drills have evidence.

### PI-OPS-001 — Package native Pi services, udev and Caddy

**Goal:** install native systemd services with least privilege. **Implementation notes:** separate users, stable adapter match, Caddy only fronts loopback SvelteKit. **Dependencies:** PI-API-001, PI-WEB-002. **Hardware required:** Pi required; controller optional. **Size:** L.

- Installer creates documented layout, users/groups, ownership and runtime socket directory.
- udev rule requires approved VID/PID and serial number and creates `/dev/cs71` only for that adapter.
- `cs71d` has serial-device access; SvelteKit lacks it but can connect to socket.
- Caddy cannot route to `cs71d`; a network scan confirms daemon has no TCP listener.
- Service sandbox settings are verified against a functional simulated smoke test.

### PI-OPS-002 — Implement backup, upgrade and rollback procedures

**Goal:** preserve durable state through maintenance. **Implementation notes:** use SQLite-consistent backup; restore in stopped service order. **Dependencies:** PI-OPS-001, PI-DOMAIN-002. **Hardware required:** Pi required; controller optional. **Size:** M.

- Backup manifests include version/checksum and both databases/configuration without secrets in logs.
- Restore passes SQLite integrity and application read-only smoke checks.
- Upgrade applies migrations then starts daemon before web; failed pre-irreversible upgrade rolls back artifacts/data.
- Low-disk, backup and journal failure produce health evidence and block new motion as designed.

## Epic PI-SWQ — Software qualification

**Outcome:** all non-hardware behavior is objectively evidenced. **Dependencies:** PI-UI-002, PI-OPS-002. **Out of scope:** declaration of physical safety or hardware acceptance. **Epic exit criteria:** release-quality automated/software evidence, security review, a11y review and controlled simulated soak are complete; unresolved defects are dispositioned.

### PI-SWQ-001 — Qualify contract, stress and failure behavior

**Goal:** execute software quality matrix against deterministic environments. **Implementation notes:** capture versions/seeds and enforce flake policy; run the declared performance profile on Raspberry Pi 5 without requiring a controller. **Dependencies:** PI-UI-002, PI-OPS-002. **Hardware required:** Raspberry Pi 5 required for NFR-01/NFR-02; controller not required. **Size:** L.

- Property tests cover request-id wrapping, stale generations, idempotency conflicts and bounded event overflow.
- Stress tests demonstrate bounded queues/subscribers and no duplicate operation execution under concurrent BFF load.
- Fault injection covers journal I/O, daemon reconnect and BFF restart with expected `UNCERTAIN` behavior.
- Under the versioned Raspberry Pi load profile, accepted priority-stop admission is reported within 250 ms; the report excludes and separately labels firmware/physical stop time (NFR-01).
- Under the same versioned profile, at least 99% of internal snapshot reads complete within 100 ms with sample count and percentile method recorded (NFR-02).
- Test report labels every result simulator/software-only and retains seed/artifact references.

### PI-SWQ-002 — Qualify security and accessibility controls

**Goal:** review attack controls and operator usability. **Implementation notes:** include manual assistive-technology sample as release input. **Dependencies:** PI-SWQ-001. **Hardware required:** No. **Size:** M.

- Automated tests cover authentication, session revocation, RBAC, CSRF, rate limits and secret redaction.
- Socket exposure/service-permission verification is recorded for staging Pi profile.
- MVP browser flows meet automated WCAG 2.2 AA checks and keyboard navigation checks.
- Manual review documents stop/recovery/fault announcements and any remediation before pilot.

## Epic PI-HIL — Hardware qualification

**Outcome:** physical behavior is measured on an approved rig; no software-only evidence is substituted. **Dependencies:** PI-SWQ-002, approved firmware/rig. **Out of scope:** certification of E-stop by software. **Epic exit criteria:** all required HIL records pass, DTR decision is documented and any failed case constrains release profile.

### PI-HIL-001 — Execute DTR and real serial protocol qualification

**Goal:** close or explicitly fail the Linux DTR gate for exact equipment. **Implementation notes:** follow the mandatory experiment plan with instrumented measurements and energy safeguards. **Dependencies:** PI-SWQ-002. **Hardware required:** Yes. **Size:** L.

- Evidence records Pi OS/kernel, adapter VID/PID/serial, controller/firmware, wiring and instruments.
- Idle, motion, close/open, daemon restart, BFF crash and USB reconnect DTR/reset cases have measured pass/fail results.
- Real controller v1/v2/CRC transcript parity is compared with expected protocol behavior.
- Result is recorded as passed with qualified policy or remains NOT_EXECUTED/failed with unattended deployment blocked.

### PI-HIL-002 — Execute motion, stop, fault and recovery HIL cases

**Goal:** measure trusted terminal versus physical behavior under controlled conditions. **Implementation notes:** technician uses physical safeguards and records raw data; software stop is not E-stop evidence. **Dependencies:** PI-HIL-001. **Hardware required:** Yes. **Size:** L.

- HIL records home/sort, injected fault, USB loss, daemon restart, web restart, power/reboot and recovery outcomes.
- Priority software-stop admission, protocol terminal and observed physical-stop timings are measured separately under defined load; daemon admission is compared with NFR-01 without treating it as physical-stop evidence.
- Any missing/ambiguous terminal or transport fault yields documented `UNCERTAIN`, not success.
- Evidence explicitly states physical E-stop was tested independently or remains outside scope.
- A soak case records duration/load, faults, reconnects, storage health and exit condition.

## Epic PI-PILOT — Pilot and release

**Outcome:** a constrained deployment is operated with evidence, rollback and safety controls. **Dependencies:** PI-HIL-002. **Out of scope:** broad production rollout or removal of physical safeguards. **Epic exit criteria:** pilot acceptance, runbook drill, issue disposition and release decision are recorded by authorized owners.

### PI-PILOT-001 — Run controlled operator pilot and drills

**Goal:** validate procedures with authorized operators on qualified equipment. **Implementation notes:** limit users/duration/scope; collect no claim beyond evidence. **Dependencies:** PI-HIL-002. **Hardware required:** Yes. **Size:** M.

- Pilot entry checklist verifies DTR decision, HIL evidence, backups, TLS/profile, user provisioning and physical safeguards.
- Operators complete login, observe, command, stop, fault/recovery and audit workflows using approved runbooks.
- Upgrade/rollback and backup/restore drills complete with recorded outcomes.
- Issues are triaged with severity, owner and release disposition before exit.

### PI-PILOT-002 — Assemble release evidence and decision record

**Goal:** make the release boundary auditable. **Implementation notes:** reference immutable build, migration and qualification artifacts; do not invent pass status. **Dependencies:** PI-PILOT-001. **Hardware required:** Yes. **Size:** S.

- Release record identifies artifact revisions, supported hardware/profile and exact configuration assumptions.
- Traceability rows have linked test/gate evidence or explicitly unresolved status.
- All critical safety/security defects are closed or release is blocked with documented reason.
- Release statement distinguishes software stop from E-stop and excludes simulator evidence from hardware claims.

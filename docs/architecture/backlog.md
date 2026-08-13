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
`NOT_EXECUTED`. That same constant is now readable over the API at
`GET /v1/system` (PI-UI-002), so the web system view can show it without
duplicating it — the gate itself is unchanged by exposing its status.

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

**Status:** Implemented as `cs71d.adapters`. The serial worker gathers the
required snapshots — advertised capabilities and observed status — before
publishing `READY`, and re-observes them after each completed movement, so the
daemon validates against what the controller reports rather than what it
assumes. Home accepts only the three axes and only when the controller
advertises the matching homing capability; sort is bounded by the advertised
`slot_max` and refused while the sorter position is unknown; both checks run
against the frozen admission view before any serial I/O. Trusted terminal
fields are recorded on the operation through schema migration 2.

Feed is refused outright: `FEED_LIFECYCLE_GATE` is `NOT_EXECUTED` and no
firmware build advertises a v2 feed lifecycle, so a feed request returns
`UNSUPPORTED` without touching the serial session. Its accepted, progress,
completion, fault, cancellation and `UNCERTAIN` coverage therefore remains
**blocked** on V2-09 and its hardware evidence; simulator runs cannot close
that gate. Home and sort are covered for all six.

- Home accepts only feeder, sorter or both and records the trusted terminal fields.
- Sort validates the advertised slot range and known sorter position before serial enqueue.
- Feed validates advertised capability and readiness; an unqualified firmware build returns `NOT_READY` or `UNSUPPORTED` without serial I/O.
- Simulator tests cover accepted, progress, trusted completion, fault, cancellation and `UNCERTAIN` for all three operation types.
- No API or BFF input can provide an arbitrary protocol payload.

### PI-DOMAIN-004 — Implement session and configuration operations

**Goal:** give the contract's session and configuration resources a domain to answer for. **Implementation notes:** connect and recover are durable operations that ask the serial worker to establish or replace its session; recover requires explicit operator confirmation; configuration is daemon policy and never reaches the controller. **Dependencies:** PI-DOMAIN-003. **Hardware required:** No. **Size:** M.

**Why this exists:** `/v1/session/connect`, `/v1/session/recover` and
`/v1/configuration` are frozen in the executable contract and required by
PI-API-001, but no domain capability answered for them: the worker connected
only at start-up, recovery had no external trigger, and configuration had a
`data-and-persistence.md` table with no task behind it. Filling those three
endpoints at the API layer would have meant inventing behaviour at the
boundary, so the gap was raised as its own task instead.

**Status:** The session half is implemented. `OperationDomain.connect` and
`OperationDomain.recover` admit durable attributable operations even when the
session is not ready, because repairing an unready session is exactly what they
are for. The serial worker owns them in its run loop rather than dispatching
them like machine commands, since they replace the link itself: connect is
satisfied by an already verified session, while recover always starts again
from a fresh transport because the point of asking is that the current one is
not trusted. A session operation succeeds only once the session is `READY` and
its required snapshots exist, and it records the observed mode and phase as its
terminal evidence.

Configuration is implemented as `cs71d.configuration` plus schema migration 3.
Values are daemon policy — heartbeat interval, event retention, operation
retention and the maximum accepted deadline — validated against the contract's
bounds by the daemon rather than trusted from the caller. A change is admitted,
journaled and versioned like any other operation and never reaches the
controller. Its trusted terminal is the committed configuration snapshot: the
values are written first, so success can never be recorded for values that were
not stored.

- Connect and recover are durable attributable operations admitted even when the session is not ready.
- Recover requires an explicit confirmation field and never runs implicitly.
- A session operation succeeds only after the session is verified and its required snapshots exist.
- Configuration changes are validated, journaled and versioned, and never reach the controller.
- A configuration read reports the applied values and the generation that applied them.

## Epic PI-API — Internal API and SSE

**Outcome:** SvelteKit has a versioned, testable local contract. **Dependencies:** PI-DOMAIN-003. **Out of scope:** public LAN daemon access and WebSockets. **Epic exit criteria:** OpenAPI/client, headers/errors, socket-only service and bounded resumable SSE pass integration tests.

### PI-API-001 — Serve OpenAPI-backed Unix-socket REST API

**Goal:** expose daemon resources over internal HTTP/JSON. **Implementation notes:** bind only Unix domain socket with service authentication; generate TypeScript client from source contract. **Dependencies:** PI-DOMAIN-003. **Hardware required:** No. **Size:** L.

**Status:** In progress. `cs71d.api.ApiServer` serves the read side —
`/v1/health/live`, `/v1/health/ready`, `/v1/snapshot`, `/v1/operations` and
`/v1/operations/{operation_id}` — on a Unix domain socket with owner/group-only
permissions and the contract's bearer service credential on every request. The
daemon has no TCP code path at all, which a static test enforces rather than
asserting at runtime. Error codes map to the documented HTTP statuses through
one table keyed by the domain error code, so a new domain error cannot acquire
a status by default, and internal vocabulary is translated at the boundary
instead of leaking outward. Responses are checked against the frozen contract
schemas by a conformance checker that fails closed on any keyword it does not
implement, and that checker has its own negative tests.

The state-changing endpoints are served: `/v1/operations/home`, `/sort`,
`/feed` and `/v1/machine/stop` require `Idempotency-Key`,
`If-Match-Generation` and `X-Deadline-Ms`, return `202 OperationAccepted`, and
accept `*` for the generation only on a priority stop. Attribution is
validated against the contract's role vocabulary and a `viewer` is refused;
that is defence in depth, not authority, which stays with SvelteKit.

`cs71d --serve` assembles the running daemon in dependency order — journal,
then domain and serial worker, then the socket — and unwinds it in reverse, so
no new request is admitted while the worker is still finishing the one it
holds. The service credential is read from a protected file named by
`service_token_path`, never from configuration values or command-line
arguments, and is refused if other users can read it. Taking the socket path
refuses to displace a daemon that is already serving it, so a second instance
cannot silently steal the path while the first still owns the serial port.

Outstanding: `/v1/session/connect`, `/v1/session/recover` and
`/v1/configuration`, which need domain capabilities that do not exist yet. The
generated TypeScript client and its CI divergence check already exist as
`npm run check:api` in the web workspace.

- Daemon starts with no TCP listener and socket mode/group excludes browser users.
- Snapshot, connect, recover, stop, home, sort, feed, operation and health resources conform to generated OpenAPI tests.
- State-changing endpoints require idempotency, generation and finite-deadline headers.
- Error codes map to documented HTTP status without exposing raw secrets or protocol internals.
- Generated TypeScript changes fail CI when contract and client diverge.

### PI-API-002 — Implement resumable bounded SSE event stream

**Goal:** publish daemon events without serial-worker backpressure. **Implementation notes:** retain daemon event ring; keep daemon `event_id` distinct from protocol `request_id`. **Dependencies:** PI-API-001. **Hardware required:** No. **Size:** M.

**Status:** Implemented as `cs71d.events.EventRing` and the `/v1/events`
stream. Publishing never blocks: events are produced on the serial worker
thread, so a subscriber that stops reading is dropped rather than allowed to
apply backpressure to the machine. Loss is always explicit — a subscriber that
overruns its bounded queue, resumes from a cursor the ring no longer retains,
or presents a cursor from a previous daemon life is told to reconcile from a
snapshot instead of being handed an incomplete sequence. Heartbeats are emitted
on the configured interval and carry the current generation without moving it.

Retention is in memory only. The `machine_events` table remains unwritten, so
replay does not survive a restart: a resumed cursor from a previous life is
refused rather than mismatched, which is safe but is not the durable replay the
persistence design describes. That gap is deliberate and belongs to a later
task.

The event-type pattern in the executable contract was fixed as part of this
work: `heartbeat` was listed in `x-known-values` but could not match the
pattern, which required at least one dotted segment. The pattern now permits a
single segment, which keeps every previously valid value valid.

- Every emitted event has monotonic daemon `event_id`, UTC timestamp, type and snapshot generation.
- `Last-Event-ID` resumes retained events in order.
- A stale cursor or subscriber overflow emits `snapshot.required` and does not silently omit changes.
- Heartbeats occur under idle conditions and do not change machine generation.
- Slow/closed clients cannot block priority stop or serial worker progress.

## Epic PI-WEB — SvelteKit platform and authentication

**Outcome:** a secure SSR BFF safely represents local operators. **Dependencies:** PI-FOUNDATION-002. **Out of scope:** daemon serial logic and public API exposure. **Epic exit criteria:** local provisioning, login/session/RBAC/CSRF and web database ownership are tested server-side.

### PI-WEB-001 — Implement local authentication and sessions

**Goal:** secure local user lifecycle and server sessions. **Implementation notes:** Argon2id hashes, opaque server-side sessions, one-time bootstrap admin. **Dependencies:** PI-FOUNDATION-002. **Hardware required:** No. **Size:** L.

**Status:** Complete for the software criteria below. The runtime dependencies
were chosen and verified first, which the web workspace previously had none of.

`@node-rs/argon2` provides the Argon2id hashes. It ships prebuilt arm64 and x64
binaries, so nothing compiles on the appliance; the alternative `argon2`
package needs a node-gyp toolchain on the device, and a pure-WASM hash is too
slow to run honest parameters on a Pi. `better-sqlite3` opens `web.db`: its API
is synchronous, which suits SSR request handling, and it is stable rather than
experimental — Node's built-in `node:sqlite` would have avoided a dependency
but would have pinned the appliance to an experimental API across minor Node
releases.

Both were installed and exercised on a development machine: Argon2id hashes
verify, SQLite reads and writes, and `npm audit --omit=dev` reports no
vulnerabilities in the runtime tree.

The server-side authentication core is now implemented in
`appliance/web/src/lib/server/auth/`: `web.db` with forward-only checksummed
migrations, the Argon2id policy, opaque server-side sessions, local accounts
and one-time expiry-bound bootstrap provisioning. Evidence class is software
only; 124 vitest tests pass, alongside `check:api`, `lint`, `check` and
`build`.

Several invariants are enforced by SQLite itself rather than by application
convention, so a future bug cannot quietly violate them: a stored password must
be an Argon2id encoding, a revoked session cannot be restored, a session cannot
be rebound to another account or token, a bootstrap token can be claimed once,
and provisioning cannot be re-opened in place.

The browser-facing boundary is `hooks.server.ts` with the login and logout
routes. It denies by default: a route is reachable without a session only by
appearing in the public list, so a page added later is protected by omission
rather than exposed by it. The session cookie is `HttpOnly`, `SameSite=Strict`
and root-scoped everywhere, and `Secure` with the `__Host-` prefix in
production. The login redirect carries a fixed reason code and never a
caller-supplied return path, so the login page cannot become an open redirect.

Two things this task does not carry. Per-session CSRF tokens, role enforcement
and login rate limiting are PI-WEB-002; SvelteKit's own cross-origin form
rejection is the only CSRF control in place so far. The operator-facing command
that prints a bootstrap token belongs with the installer in PI-OPS-001; until it
exists a token can only be issued programmatically, so a fresh appliance is
reachable only through that packaging work.

- [x] Fresh installation has no usable default password and requires expiry-bound bootstrap provisioning.
- [x] Password storage uses Argon2id; plaintext passwords/tokens never appear in database/log tests.
- [x] Cookies are Secure/HttpOnly/SameSite according to production origin policy and sessions rotate on login.
- [x] Logout, expiry, disable and password-change revoke existing sessions.
- [x] `web.db` access is exclusive to web service identity.

### PI-WEB-002 — Enforce server-side RBAC and CSRF

**Goal:** protect all BFF state-changing routes. **Implementation notes:** roles map to documented matrix; UI visibility is supplementary only. **Dependencies:** PI-WEB-001. **Hardware required:** No. **Size:** M.

Role enforcement is implemented. The RBAC matrix in
`docs/architecture/security-and-safety.md` is transcribed once, as data, in
`appliance/web/src/lib/server/auth/capabilities.ts`, and routes ask for a
capability rather than for a role, so the table has exactly one implementation.
Its spec asserts every role and capability pair against a hand-written copy of
the documented table rather than against the implementation, and names the two
rows that are easiest to get wrong: a viewer may stop the machine, and no role
at all — administrator included — may drive the protocol or the device path.

`policy.ts` declares what each route requires, keyed by route id, and
`hooks.server.ts` authorizes against it before any page or action runs. A route
with no declaration is refused rather than resolved, and `policy.spec.ts` scans
the route directory so a page added without a declaration fails the build
instead of being discovered in production. A path that matched no route is left
to answer as missing; a permission error there would only mislead. Handlers
whose actions differ in privilege from the page call `requireCapability` next to
the effect, which is where PI-BFF-001 will authorize before the daemon call.
Pages are told what the role may do so they can show the right controls; that
list is a reflection of the server's decision and never a substitute for it.

Forgery and cost are handled in the same hook, before any handler runs and
cheapest check first, so a forged or oversized request costs a header comparison
rather than a database read or an Argon2id hash. A state-changing request must
name the appliance's own origin — a missing `Origin` header is refused rather
than excused — and must echo back a token a forging page cannot read. For a
signed-in browser that token is derived from the session token by HMAC, so
nothing extra is stored, it rotates with the session, and a stolen `web.db`
still yields nothing; a browser with no session gets a random token in its own
`HttpOnly` cookie, which the login form echoes back, because signing in is
state-changing too.

The documented budgets are 30 state-changing requests per minute per address,
five sign-in attempts per minute per account and per address, two password
hashes in flight at once, and a 16 KiB declared form body. The concurrency limit
refuses rather than queues: an Argon2id hash costs 64 MiB, and a queue would
turn a burst into memory pressure plus a delay. The body check reads the
declared length as an early refusal; the enforcement that cannot be lied to is
the adapter's `BODY_SIZE_LIMIT`.

- [x] Tests exercise each allowed and denied role/action pair, including Viewer software stop.
- [x] State-changing forms/actions reject missing/invalid CSRF and invalid Origin policy.
- [x] Server handlers, not client code, perform authorization before daemon call.
- [x] Login and command endpoints apply documented size/rate controls.

The command endpoints themselves do not exist yet; the controls above apply to
every state-changing route by default, so PI-BFF-001 inherits them rather than
having to remember them.

## Epic PI-BFF — BFF integration

**Outcome:** SvelteKit translates authorized intent to local daemon operations and safely bridges events. **Dependencies:** PI-API-002, PI-WEB-002. **Out of scope:** direct browser daemon calls and client-side operation completion. **Epic exit criteria:** BFF is socket-only, preserves semantic headers and survives web restarts without serial impact.

### PI-BFF-001 — Implement generated daemon client and command translation

**Goal:** call `cs71d` from SvelteKit server actions. **Implementation notes:** pass authenticated actor attribution, idempotency, generation and deadline; never forward browser raw commands. **Dependencies:** PI-API-002, PI-WEB-002. **Hardware required:** No. **Size:** M.

`appliance/web/src/lib/server/daemon/` holds named commands with typed
arguments over the Unix domain socket. There is no host, no port and no URL in
it: the socket path comes from configuration and the request path is a literal
the client builds, and there is no method that takes a path, a device or a
protocol string. Arguments are validated before anything is sent, and the
response types come from the generated contract, so contract drift fails the
build rather than a machine.

Every command carries an idempotency key, a generation to match and a deadline.
The software stop matches any generation, which the contract allows there and
nowhere else: a stop refused because the page was a few seconds old would be a
stop that did not happen. An accepted command returns an `operation_id` and a
pending state and is never a claim that the machine acted.

Daemon errors are translated rather than forwarded: the server keeps the code,
request id and daemon words for correlation, and the browser gets wording this
workspace wrote. A daemon `401`/`403` means the service credential is wrong, so
the operator is told the service is unavailable rather than that their account
was refused. The credential is read from a protected file named by
`CS71_WEB_SERVICE_TOKEN_PATH`, never from an environment value, and a file other
users can read is refused rather than repaired.

The dashboard reads the snapshot in its `load` and submits the software stop as
a named action. Authorization happens in the action, next to the effect, rather
than being inferred from the fact that a page rendered a button. A daemon that
is not answering makes the page report that rather than fail, because an
operator whose machine has gone quiet still needs the screen and the stop
control has to stay on it. The stop never reuses an idempotency key:
deduplication is for a resubmitted intent, and a replayed key would let the
daemon answer with the first result and turn a stop into a no-op. What the page
shows is an `operation_id` and a pending state, labelled as an acceptance and
not a completion.

Every command writes a `web_audit` row in `web.db` — accepted, refused or never
answered — carrying the actor, this service's request id, the `operation_id` and
the daemon code that explains a refusal. It is attribution, not the machine's
journal of record: `cs71d` owns what the machine did in its own database, and
the two are never written in one transaction. Entries cannot be edited, and the
table has no column for a password, a token or a form body.

Remaining: configuration has a client but no screen; no backlog item yet owns
it. Connect, home, sort, feed and recovery are now screens, delivered in
PI-UI-001 and PI-UI-002.

- [x] Browser requests cannot select daemon URL, serial path or arbitrary protocol command.
- [x] BFF supplies required command headers and maps daemon errors to safe user responses.
- [x] Accepted response displays `operation_id`/pending state, not completion.
- [x] BFF audit entries correlate actor, request and `operation_id` without shared database writes.

### PI-BFF-002 — Implement browser SSE bridge and restart isolation

**Goal:** maintain browser view from daemon events/snapshots. **Implementation notes:** BFF fans out/reconnects; it does not own/retry machine operations. **Dependencies:** PI-BFF-001. **Hardware required:** No. **Size:** M.

Complete for its software criteria. `events.ts` is the only place that
reconnects to `cs71d` on its own, which is safe because reading is the only
thing that can be retried without asking whether the machine already acted; it
re-attaches a reader and never resends a command. `broadcast.ts` turns that one
reader into as many browser streams as are open, attaching on the first
subscriber and letting go after the last, and `GET /events` serves them as SSE
behind `machine.read`.

Nothing in the fan-out waits for a browser: one that falls behind has its
backlog discarded and is told to read a snapshot, and one that vanishes cannot
slow daemon event production or the serial worker. A cursor is honoured only
when the whole run since it can be handed over; absent, unparseable, too old or
from before a restart, it opens with a `resync` instead. Only real events carry
an SSE `id:`, so a notice never becomes a cursor. Daemon `event_id`,
`operation_id` and `generation` reach the browser unrenumbered, and an unknown
event type is passed through as the contract requires.

`machine-view.svelte.ts` is what the browser does with it, and it builds no
machine state out of an event: an event means the screen is behind, and being
behind is resolved by reading a snapshot through the page's own server load.
Reads are coalesced, and a screen that owes a snapshot says so. A restart of
this service drops a database handle and a reader and nothing else, which is
why it can neither cancel nor duplicate an operation `cs71d` is running.

- [x] Browser reconnect after event overflow fetches snapshot before presenting incremental updates.
- [x] BFF restart during a simulated daemon operation neither cancels nor duplicates it.
- [x] Browser event disconnect does not block daemon event production or serial worker.
- [x] UI-facing events preserve daemon `event_id`, `operation_id` and generation semantics.

## Epic PI-UI — Operator UI

**Outcome:** operators can safely complete MVP workflows with clear uncertainty. **Dependencies:** PI-BFF-002. **Out of scope:** raw protocol console, classifier workflow and native mobile app. **Epic exit criteria:** MVP pages pass browser/a11y flows and never make premature completion or E-stop claims.

### PI-UI-001 — Build dashboard, manual and operations views

**Goal:** deliver status, command and history screens. **Implementation notes:** pages are SSR first and capability-driven; expose snapshot generation to command forms. **Dependencies:** PI-BFF-002. **Hardware required:** No. **Size:** L.

Delivered. The dashboard is delivered: `machine-status.ts` decides every
sentence a screen may say about the machine — acceptance is never worded as
completion (`COMPLETION_WORDS` is the testable form of that rule), a terminal
without `trusted_terminal` is presented as an outcome that is not known rather
than repeated by its state name, and an axis the session has never observed
reads "not known" rather than "not homed", because the wire has no third value
and a guess in the safe-looking direction is still a guess. `UNCERTAIN` carries
its own tone, distinct from ordinary attention. The software stop renders first
in document order with no positive `tabindex` anywhere, and
`dashboard-page.spec.ts` drives the keyboard flow end to end: the real load
renders the page, the first focusable control is the stop, and submitting
exactly the fields its form carries reaches the daemon as a stop command.

The manual controls are delivered on the same page. `machine-controls.ts`
decides, from the snapshot the operator is looking at, which of connect, home,
sort and feed may be offered and what is said beside a withheld one: no
command form exists at all when the machine has not been read, motion is
withheld without a `READY` session or when the daemon reports itself not
ready, an axis the firmware does not advertise is withheld by that name, and
the sorter slot list is exactly `slot_count` long. The feed control follows
the capability rather than a UI opinion — today that means disabled, with the
daemon's `feed_unavailable_reason` shown verbatim, and it enables only when a
qualified firmware ever advertises feeding. Every command form carries the
snapshot generation the operator decided against and an idempotency key minted
for that render, so a stale page is refused by the daemon with reload wording
and a resubmitted form is the same command, not a second one; the end-to-end
spec proves the served fields alone reach the daemon as those headers. Each
attempt — accepted, refused by the daemon, or refused by this workspace's own
form checks before anything was sent — lands in `web_audit` under
`machine.connect|home|sort|feed`.

The operation history screen is delivered at `/operations`, gated by the same
`machine.read` capability as the dashboard. `operation-history.ts` reuses
`machine-status.ts`'s `operationReading` for every row rather than
reimplementing its wording, so the two screens cannot describe the same
operation two different ways — an unsettled row is never worded as a
completion and a terminal without `trusted_terminal` still reads as an
outcome that is not known. The state and type filters offer exactly the
values the contract defines, nothing invented, and are a `GET` query string
rather than a form post: there is no action here to audit, so a bookmarked or
shared URL reproduces the same page. A filter value this workspace does not
recognise is dropped rather than sent to the daemon, and the daemon's own
opaque `next_cursor` carries the current filter forward for the next page.

- [x] Dashboard shows connection, homing, active operation, faults and snapshot generation from daemon snapshot.
- [x] Manual controls validate capability/slot and submit idempotency/generation-protected intent.
- [x] Feed controls are capability-driven and remain unavailable with an explicit reason until the firmware feed lifecycle gate is qualified.
- [x] Operation history shows accepted, progress and terminal states with trusted-terminal status.
- [x] UI never labels HTTP acceptance as machine completion.
- [x] Keyboard-only automated flow can find and activate software stop.

### PI-UI-002 — Build fault, recovery and system views

**Goal:** make unsafe state and maintenance evidence actionable. **Implementation notes:** recovery/reset requires administrator confirmation; no raw serial access. **Dependencies:** PI-UI-001. **Hardware required:** No. **Size:** M.

Delivered. `[data-tone="uncertain"]` is now styled distinctly from
`[data-tone="attention"]` — bold, bordered and filled rather than color alone
— so a machine whose state is not known cannot be mistaken at a glance for one
with an ordinary known problem; the two never collapse into the same visual
weight. Dependent motion was already withheld whenever a session is not
`READY` or an axis is unadvertised (PI-UI-001's `machine-controls.ts`), which
already covers a session the daemon has never observed; that coverage is now
asserted directly for `UNCERTAIN`.

Recovery is a new control on the dashboard, gated to `machine.recover`
(administrator-only) and decided independently of the operator controls by
`recoveryPlan()`: it is the way back from a session that is not known, and a
deliberate reset of an otherwise healthy one, withheld only while a session is
already being established or is already recovering. The form requires an
explicit confirmation checkbox this workspace validates on the server before
anything reaches the daemon — a request that arrives without it is refused as
invalid, not silently coerced into consent — and every attempt lands in
`web_audit` under `machine.recover`, accepted or refused.

The system view at `/system` shows the firmware and protocol version already
in the snapshot, journal health inferred from recorded faults and captioned as
inferred rather than a dedicated check, storage health explicitly reported as
not available from this service (no daemon data source exists for it yet),
and DTR-gate status from a new `GET /v1/system` endpoint this slice adds to
the contract and to `cs71d`: it serializes the existing
`cs71d.device.DTR_GATE_STATUS` constant, today `NOT_EXECUTED`, worded as the
project's own evidence-status legend and never presented as a pass — the same
caution the daemon has always applied to opening a real serial port on POSIX.

- [x] `UNCERTAIN` is visually and semantically more prominent than ordinary status.
- [x] Dependent motion controls are disabled when readiness/homing/capabilities are unknown.
- [x] Recovery/reset prompt requires explicit administrator confirmation and records outcome.
- [x] System view shows versions, storage/journal health and DTR-gate status without claiming it passed.

## Epic PI-OPS — Deployment and operations

**Outcome:** appliance installation is repeatable and observable. **Dependencies:** PI-API-001, PI-WEB-002. **Out of scope:** containers and Internet-hosted service. **Epic exit criteria:** clean Pi install, restricted services/socket/device mapping, backup/restore and rollback drills have evidence.

### PI-OPS-001 — Package native Pi services, udev and Caddy

**Goal:** install native systemd services with least privilege. **Implementation notes:** separate users, stable adapter match, Caddy only fronts loopback SvelteKit. **Dependencies:** PI-API-001, PI-WEB-002. **Hardware required:** Pi required; controller optional. **Size:** L.

Delivered for the evidence class this repository can produce without a Pi:
`appliance/ops/install.sh` creates the documented users (`cs71d`, `cs71-web`),
group (`cs71-api`), directories and their ownership, generates one shared
service credential and writes it to each side's own copy
(`/etc/cs71d/service-token`, `/etc/cs71-web/service-token` — two files, not
one, because `deployment-and-operations.md`'s layout table only names the
web's copy), and installs `cs71d.service`/`cs71-web.service`/a `caddy.service`
drop-in. `cs71d.service` runs as `cs71d` with effective group `cs71-api`, so
the socket it creates comes out `cs71d:cs71-api` mode `0660`, reachable by
`cs71-web` through that one shared group and by nothing else; `machine.db`
stays protected by its own `0600` file mode regardless of group. Neither
service can gain privileges, write outside its own state directory, or open
capabilities; only `cs71d.service` may touch `/dev/cs71`
(`DeviceAllow=/dev/cs71 rw`); `RestrictAddressFamilies=` is narrowed to
`AF_UNIX` for the daemon and `AF_UNIX AF_INET AF_INET6` for the web service,
which needs loopback HTTP for Caddy to reach. The udev rule matches vendor
ID, product ID *and* serial number — not vendor/product alone — and ships
with `@@VENDOR_ID@@`/`@@PRODUCT_ID@@`/`@@SERIAL@@` placeholders that match no
real device until `install.sh` substitutes them from hardware evidence no
adapter has produced yet (PI-HIL-001). The Caddyfile is one site block
proxying only to loopback SvelteKit; there is no route to the socket, the
daemon API or a SQLite file anywhere in it.

`appliance/ops/tests/smoke-test.sh` is a *functional*, not merely structural,
smoke test: on a real Linux host (the `appliance-ops` CI job, `ubuntu-latest`)
it runs the real installer, starts the real services under their unmodified
sandbox directives — against the simulator backend and with the
udev/device-arrival gate removed, since `backend = "serial"` cannot start on
any Linux host while Linux DTR is `NOT_EXECUTED` (SAF-07), Pi included — and
proves for real: the socket's owner and mode; that the `cs71-web` identity
can reach the daemon through it and get a real answer; that the same identity
cannot read the serial-device stub; that Node stays up under
`ProtectSystem=strict` and the rest; and that a process carrying the daemon's
own sandbox properties is kernel-refused when it tries to open an `AF_INET`
socket — stronger evidence than an after-the-fact network scan, since it is
a prevention proof rather than an absence observation.
`appliance/ops/tests/test_artifacts.py` cross-checks every artifact against
the paths `cs71d/config.py` and `web/config.ts` actually enforce, so a change
to either would fail this instead of silently drifting.

What remains genuinely hardware-gated, and is not claimed here: a real Pi
install/reboot/backup drill (ADR-0009's own revisit trigger), the approved
adapter's actual VID/PID/serial, and closing the Linux DTR gate — all
PI-HIL-001 and a future Pi-install drill, not software work.

- [x] Installer creates documented layout, users/groups, ownership and runtime socket directory.
- [x] udev rule requires approved VID/PID and serial number and creates `/dev/cs71` only for that adapter.
- [x] `cs71d` has serial-device access; SvelteKit lacks it but can connect to socket.
- [x] Caddy cannot route to `cs71d`; a network scan confirms daemon has no TCP listener.
- [x] Service sandbox settings are verified against a functional simulated smoke test.

### PI-OPS-002 — Implement backup, upgrade and rollback procedures

**Goal:** preserve durable state through maintenance. **Implementation notes:** use SQLite-consistent backup; restore in stopped service order. **Dependencies:** PI-OPS-001, PI-DOMAIN-002. **Hardware required:** Pi required; controller optional. **Size:** M.

`appliance/ops/backup.sh` takes a SQLite-consistent online backup
(`sqlite3 ... .backup`, no service stop needed) of both `machine.db` and
`web.db` plus `cs71d.toml`/`web.env`, into a timestamped
`/var/lib/cs71-backups` directory with a checksummed `manifest.json`
(`appliance/ops/lib/manifest.py`) carrying the source commit and each
workspace's own version — read from a `release-info.json` `install.sh`/
`upgrade.sh` write at build time, not introspected live, since a periodic
timer run has no checkout beside it. No service-token file is ever included.
`systemd/cs71-backup.timer` runs it daily; `install.sh` copies `backup.sh`
and the manifest helper to `/opt/cs71/ops` so the timer survives the
checkout moving or updating.

`appliance/ops/restore.sh --from <dir>` verifies the manifest's checksums and
each database's `PRAGMA integrity_check` before touching anything live,
stops web then daemon, installs the databases and configuration, then starts
the daemon and proves it answers over its own socket before starting the web
service and proving the same over its loopback port — the integrity-check-
then-read-only-smoke-test contract in `data-and-persistence.md`.
`appliance/ops/upgrade.sh` backs up first, stops web then daemon, rebuilds
both workspaces from the current checkout (the same `build_web_workspace`/
`build_daemon_venv` `install.sh` itself calls), then starts daemon then web
the same validated way. There is no separate migration-runner command in
this codebase — opening `machine.db`/`web.db` *is* the forward-only,
checksummed migration — so "apply migrations" and "start daemon"/"start web"
are the same step. Any failure before the web service is confirmed healthy
rolls the release artifacts back and calls `restore.sh` against the
pre-upgrade backup; a failed rollback stops and says so rather than guessing
further, which is what the deployment runbook owns from there.

`cs71d`'s production profile also carries a `DurabilityMonitor`
(`appliance/daemon/src/cs71d/storage_health.py`) that latches the machine
exactly the way a failed journal write already does — `journal_available`
false, `/v1/health/ready` reporting `ready: false` with a reason, new
operations refused — when free space beside `machine.db` falls under a fixed
500 MiB floor, or `backup-status.json` (which `backup.sh` writes on every
attempt, success or failure) is missing, records a failure, or is older than
48 hours. The check runs on every readiness poll and before every admission,
so the fault is visible even with no traffic, and does not clear itself —
the same conservative, restart-to-clear posture the existing journal fault
already has. Development and test profiles carry no monitor: their
throwaway paths have no installed backup timer behind them.

`appliance/ops/tests/smoke-test.sh` runs `backup.sh` and `restore.sh` for
real on the CI Linux host, against the real databases its own install
already wrote — including pointing the daemon's real, fixed unit names at
the same simulator-backed content the rest of the smoke test already proved
healthy, so `restore.sh` runs completely unmodified through the exact unit
names a real restore would use. `upgrade.sh`'s ordering, backup-before-
mutation sequencing and rollback-through-`restore.sh` path are checked
structurally in `appliance/ops/tests/test_artifacts.py` rather than rebuilt
end-to-end in CI, since that would only repeat `install.sh`'s own build under
a slower name.

What remains genuinely hardware-gated, and is not claimed here: a real Pi
backup/restore/upgrade drill against the production profile and a real
controller — PI-HIL-001 and a pilot-gate drill (`roadmap.md`), not software
work.

- [x] Backup manifests include version/checksum and both databases/configuration without secrets in logs.
- [x] Restore passes SQLite integrity and application read-only smoke checks.
- [x] Upgrade applies migrations then starts daemon before web; failed pre-irreversible upgrade rolls back artifacts/data.
- [x] Low-disk, backup and journal failure produce health evidence and block new motion as designed.

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

## Epic PI-VISION — On-appliance headstamp classification

**Outcome:** manufacturer headstamp classification runs on Uno + Raspberry Pi 5 only, with no separate Windows PC, through a confidence-gated hybrid-autonomy model that starts fully manual and only earns autonomy from recorded evidence. **Dependencies:** PI-OPS-001, PI-API-001, PI-WEB-002, PI-DOMAIN-004. **Out of scope:** closing the feed lifecycle gate itself (`FEED_LIFECYCLE_GATE`, tracked under PI-DOMAIN-003/PI-HIL, only consumed here), producing a specific production-quality model (accuracy is ongoing evidence, not a one-time deliverable), and any claim that primer-presence detection may authorize autonomous action — that is permanently excluded, not a future milestone. **Epic exit criteria:** self-labeled data collection runs in production sorting; a read-only classifier reports recorded per-class accuracy; confidence-gated autonomy for manufacturer routing ships with rollback; the primer-presence axis always forces operator confirmation with no configuration path around it; the operator-facing train/activate lifecycle ships with versioned rollback. See [ADR-0013](adr/0013-vision-classifier-service-and-hybrid-autonomy.md) for the accepted design and its rejected alternatives.

### PI-VISION-001 — Package `cs71-vision` service skeleton and camera capture

**Goal:** stand up the third appliance service with least-privilege systemd packaging and a working capture pipeline. **Implementation notes:** plain V4L2 against the USB VGA UVC module in `3DModels/Classifier/CameraV2`; extend `appliance/ops/install.sh`'s pattern rather than duplicating it. **Dependencies:** PI-OPS-001. **Hardware required:** Pi and the camera module for a real capture; not the controller. **Size:** M.

Delivered for the evidence class achievable without a Pi or real camera.
`appliance/vision/` is a new Python workspace: `cs71vision.camera.Camera` is
a two-method protocol with `FixtureCamera` (deterministic, dependency-free —
the same evidence role `cs71d`'s simulator plays) and `V4L2Camera` (opened
through OpenCV's V4L2 backend, `cv2.VideoCapture(path, cv2.CAP_V4L2)`, not a
hand-rolled ioctl implementation — every small "V4L2 Python bindings"
package that exists to avoid writing that protocol by hand is itself
GPL-licensed, a direct transcription of the GPL `linux/videodev2.h` kernel
header, which does not fit this workspace's MIT license; OpenCV is
Apache-2.0 and removes both that licensing question and the real correctness
risk of reconstructing exact kernel-ABI struct layouts from memory with no
real device to test against). `cs71vision.runtime.CaptureLoop` polls one
`Camera` on its own thread and survives both a camera read failure and a
sink that raises. `cs71vision.config` mirrors `cs71d.config` exactly: three
profiles, a fixed production device path (`/dev/cs71vision`), refusal of
unknown fields. `appliance/ops/systemd/cs71-vision.service` follows
`cs71d.service`'s sandbox pattern, tightened further — `RestrictAddressFamilies=`
is empty, not even `AF_UNIX`, since this slice has no socket of its own yet
— and gates on its own camera symlink the same way `cs71d.service` gates on
the controller. `appliance/ops/udev/98-cs71-vision.rules` matches vendor and
product ID only, not a serial number like the controller's rule, because
this matches one specific already-chosen camera module rather than a broad
device class, and cheap UVC modules do not reliably program a unique serial
in the first place; `install.sh`'s `--camera-vendor-id`/`--camera-product-id`
are optional so every existing caller (`tests/smoke-test.sh` included) keeps
working unmodified. `tests/smoke-test.sh` now also derives a
`cs71-vision-smoke.service` on the fixture backend, starts it for real, and
confirms it is actually capturing (not just active) and that its sandbox
refuses even an `AF_UNIX` socket.

What remains genuinely hardware-gated, and is not claimed here: `V4L2Camera`
is unit-tested only against its own refusal path (a device it cannot open);
nothing in this repository proves it opens a real camera correctly. Real
camera evidence is PI-HIL/hardware-evidence territory, the same class of gap
the DTR gate already is for `cs71d`.

- [x] New systemd unit follows the same least-privilege sandbox pattern as `cs71d.service`/`cs71-web.service`: own identity, no access to the serial device or either existing database.
- [x] Captures frames from a V4L2 UVC source at a configurable resolution and interval.
- [x] A fixture/simulated video source lets this be evidenced in CI without real camera hardware, matching this repository's existing simulator-evidence boundary (ADR-0010).

### PI-VISION-002 — Self-labeled dataset from ordinary manual sorting (Phase 0)

**Goal:** correlate each captured frame with the slot the operator actually chose, with zero extra operator effort. **Implementation notes:** own SQLite store, following the existing separate-ownership pattern; correlate via `operation_id`, never a second write into `machine.db`/`web.db`. **Dependencies:** PI-VISION-001, PI-API-002. **Hardware required:** Pi and camera; controller optional, since the simulator already produces trusted terminals to correlate against. **Size:** M.

Delivered for the evidence class achievable without a Pi or real camera.
A real gap surfaced while implementing this: `cs71d`'s operation contract
exposed no durable, externally readable trace of "which slot" a sort
reached — `terminal_fields` (which the protocol layer already populates,
`{"slot": "3"}`, per `ArduinoCode/PROTOCOL_V2.md`'s `sortto:`/`done:slot=`
exchange) was recorded in `machine.db` but never returned by the API. This
PR adds it to the `Operation` schema and `_operation_body()` as an additive,
optional field — the same discipline PI-UI-002's `/v1/system` addition
already established — with a daemon test driving a real sort to a trusted
terminal through the HTTP API and asserting the field is present
(`appliance/daemon/tests/test_api.py`).

`cs71vision.daemon_client.DaemonClient` is a minimal, read-only Unix-socket
HTTP client — `GET /v1/operations` only, authenticated with its own service
credential (a third copy of the shared secret, `/etc/cs71-vision/service-token`,
extending PI-OPS-001's two-file pattern to three). `cs71vision.correlator.FrameBuffer`
keeps the last 64 captured frames; `Correlator.poll_once()` asks `cs71d` for
newly `SUCCEEDED` sorts, matches each one to the frame captured at-or-before
its `created_at`, and discards — never guesses — when no frame qualifies.
`cs71vision.dataset.DatasetStore` owns `vision.db` exclusively, mirroring
`cs71d.journal.Journal`'s exact shape: forward-only checksummed migrations,
WAL mode, owner-only file mode, `operation_id` as primary key so a repeated
poll is a no-op, never a duplicate. `cs71vision.runtime.CorrelationLoop`
polls on its own thread and interval, independent of capture.

`appliance/ops/backup.sh`, `restore.sh`, `upgrade.sh` and `lib/manifest.py`
now handle `vision.db` the same way `machine.db`/`web.db` already are, but
optionally: an appliance upgraded from a pre-PI-VISION install may not have
one yet, and that must not fail a backup or restore that never needed it.
`cs71-vision.service` gained `SupplementaryGroups=cs71-api` and
`RestrictAddressFamilies=AF_UNIX` (was empty in PI-VISION-001, since it had
no socket to reach yet) and `StateDirectory=cs71-vision` for `vision.db`.
`tests/smoke-test.sh` points the derived `cs71-vision-smoke.service` at the
real, already-running `cs71d-smoke.service` and confirms it reaches it for
real (no "could not reach cs71d" error logged, `vision.db` actually created
and correctly owned) — real evidence for the socket/group wiring, not just
the fixture camera in isolation.

What remains genuinely hardware-gated, and is not claimed here: this smoke
test does not drive an actual sort operation through `cs71d-smoke` to prove
a labeled example gets recorded end to end on a real Linux host — the
`Correlator`/`DaemonClient` logic itself already has thorough unit and
integration coverage (including a real fake HTTP server over a real
`AF_UNIX` socket) that does not need real hardware to be genuine evidence,
so a live end-to-end drill was judged to add packaging-layer risk without
adding real coverage of anything not already proven. `V4L2Camera` real
camera evidence remains PI-HIL/hardware-evidence territory (PI-VISION-001's
own gap, unchanged here).

- [x] Every sort operation's trusted terminal correlates to exactly one stored frame and label, keyed by `operation_id`.
- [x] Per-class example counts are queryable without scanning raw frames.
- [x] No frame is ever stored without a corresponding durable operation record; a frame with no matching terminal is discarded, not guessed at.
- [x] The store's backup/ownership discipline matches `machine.db`/`web.db`'s (PI-OPS-002): SQLite-consistent, included in `backup.sh`'s manifest once this ships.

### PI-VISION-003 — Operator UI: dataset review and per-class counts

**Goal:** let an operator see training readiness before training is offered, not after. **Implementation notes:** reuse this workspace's wording-as-data pattern rather than inline strings. **Dependencies:** PI-VISION-002, PI-WEB-002. **Hardware required:** No. **Size:** S.

Delivered for the evidence class achievable without a Pi or real camera.
Before writing any web code, the existing daemon/BFF pattern was read end to
end (`appliance/web/src/lib/server/daemon/{client,transport,errors,credentials,index}.ts`,
`routes/system/+page.server.ts`, `lib/server/auth/policy.ts`) rather than
guessed at, per the investigation this task was explicitly asked to do
first. `cs71-vision` had no HTTP API of its own yet — only its read-only
`daemon_client` calling *out* to `cs71d` (PI-VISION-002) — so this task adds
one: `cs71vision.api.DatasetApiServer` serves a single resource,
`GET /v1/dataset`, on its own Unix domain socket, mirroring `cs71d.api`'s
shape (bearer auth against the shared service credential, stale-socket
reclaim, `AF_UNIX`-only) at a fraction of its surface — one route, no
commands, no event stream. Per-class counts come straight from
`DatasetStore.counts_by_slot()`; the class *is* the slot the operator sorted
into, the same self-labeling ADR-0013 describes, so no new labeling concept
was introduced. `cs71vision.runtime.build_api_server` gates on
`daemon_service_token_path` the same way `build_correlation_loop` already
does: no credential, no dataset ever populated, nothing to authenticate a
caller against, so capture-only mode exposes no API either — and it opens
its own independent `DatasetStore` connection rather than sharing the
correlation loop's, safe under the WAL mode `DatasetStore.open` already
enforces and simpler to reason about than a shared handle.

**Web-side scope call, made explicitly rather than assumed:** a full second
generated OpenAPI contract (mirroring `appliance/contracts/`, a TypeScript
type-generation step and a contract test suite) was weighed against a
lightweight hand-typed client for cs71-vision's one new read endpoint, and
the lightweight route was chosen — proportionate to an "S"-sized task with a
single resource. `appliance/web/src/lib/server/vision/client.ts` reuses
`daemon/transport.ts#exchange` and `daemon/credentials.ts#readServiceToken`
directly (both are generic Unix-socket-HTTP infrastructure despite living in
the daemon module, not daemon-specific), and re-wraps transport failures into
its own `VisionError` rather than leaking a `DaemonError` into a caller
talking to a different service. If the vision API surface grows with
PI-VISION-004/005 (train/activate/rollback), formalizing a real generated
contract is worth revisiting; one read-only resource does not justify it yet.

**Socket reachability, PI-VISION-002's own group grant was not enough on its
own.** `cs71-vision.service`'s `SupplementaryGroups=cs71-api` only ever let
`cs71-vision` reach *`cs71d`'s* socket as a client; it does nothing for a
socket `cs71-vision` itself creates. `Group=` became `cs71-api` as the
service's *effective* primary group instead — the same shape `cs71d.service`
itself already uses — which does both jobs with one directive: `cs71-vision`
can still reach `cs71d`'s socket, and its own new dataset-api socket now
comes out `cs71-vision:cs71-api` 0660, reachable by `cs71-web` through the
supplementary grant it already carried for `cs71d`'s socket, no new grant
needed on that side. `RuntimeDirectory=cs71-vision`/`RuntimeDirectoryMode=0750`
holds the socket, recreated fresh on every start like `cs71d.service`'s own
`RuntimeDirectory=`. `tests/smoke-test.sh` proves this for real: it waits for
`/run/cs71-vision/cs71vision.sock`, asserts its owner and mode, then — running
*as* `cs71-web` with `cs71-web`'s own copy of the shared credential — issues a
real `GET /v1/dataset` through it and checks for `200`. That is a different
claim than PI-VISION-002's own smoke check (which only proved `cs71-vision`
could reach `cs71d`), and needed its own evidence.

The dataset review page itself is `/dataset`
(`routes/dataset/+page.server.ts`/`+page.svelte`), gated by the same
`machine.read` capability `/system` uses — a read of `cs71-vision`'s state is
the same class of thing as a read of `cs71d`'s. `lib/dataset-view.ts` is the
wording-as-data module the task asked for, mirroring `system-view.ts`'s
`Reading` shape: each class becomes a `label`/`detail`/`tone`, an ineligible
class's `detail` states the floor and says why, not merely omitting it from
a list. No training control exists anywhere in this codebase yet
(PI-VISION-004/005), so "no training control is enabled until a class clears
the floor" holds by construction — there is nothing yet to wrongly enable.

What remains genuinely hardware-gated, and is not claimed here: the same
`V4L2Camera`/hardware-evidence gap PI-VISION-001 already carries, unchanged.
The dataset page itself was verified through a real SvelteKit hook, a real
authenticated session and a real stand-in `cs71-vision` on a real `AF_UNIX`
socket (`routes/dataset/dataset-page.spec.ts`) — the same evidence class
`system-page.spec.ts` already established for `/system` — not a live browser
session against real hardware, which this task never required.

- [x] UI shows the example count per class next to the configured minimum-example floor.
- [x] A class below the floor is visibly marked ineligible with the reason shown, not merely absent from a list.
- [x] No training control is enabled until at least one class clears the floor.

### PI-VISION-004 — Local training pipeline and versioned candidate models

**Goal:** implement the Pi-local background retraining job with a hard per-class data floor and recorded held-out accuracy. **Implementation notes:** this is where Pi 5's real training throughput gets measured, not assumed. **Dependencies:** PI-VISION-002, PI-VISION-003. **Hardware required:** Yes, Raspberry Pi 5 — training wall-clock time is itself part of the acceptance evidence. **Size:** L.

Delivered for the evidence class achievable without a Pi. `cs71vision.training`
is the pipeline: `extract_features` decodes a captured frame to grayscale,
resizes it to a fixed 32x32 regardless of the capture resolution a config
used, and normalizes to `[0, 1]` - a small, fixed-length, dependency-free
vector, not a learned embedding, because Phase 0 (ADR-0013) has no model and
no dataset to bootstrap one from; a better feature set can replace this
function later without touching anything downstream of it. `train_candidate`
groups examples by slot, excludes any class below
`minimum_examples_per_class` **outright** - it never reaches feature
extraction or training, not merely a UI flag - and refuses to train at all
when fewer than two classes clear the floor (a one-class model is not a
candidate PI-VISION-005 should ever be able to offer). Each included class is
deterministically split (a `random.Random(seed)` local to the call, never
wall-clock or global random state) into a training pool and a held-out
subset; held-out accuracy is computed strictly from examples the classifier
never trained on. A class with only one example after clearing the floor
still trains on it and is simply skipped for held-out evaluation, rather than
this function refusing to train at all.

**Classifier choice, made explicitly:** `scikit-learn`'s `RandomForestClassifier`
(BSD-3-Clause; its own dependencies - `scipy`, `joblib`, `threadpoolctl`,
`narwhals` - are all BSD/MIT), the same reasoning that put OpenCV over a
hand-rolled V4L2 implementation in PI-VISION-001: a well-tested, permissively
licensed, minimal-footprint library over a hand-rolled classifier, sized for
a background job training on tens to a few hundred examples per class on a
Pi 5 CPU - not a deep-learning framework sized for a workload this is not.
`pickle` serializes the fitted classifier to bytes for storage; no new
on-disk model-file format was introduced.

**Storage extends `vision.db`, not a new file or directory.** A forward-only
migration (schema version 2, `models`) adds one table: `model_blob` plus
JSON-encoded `included_classes`/`excluded_classes`/`accuracy_by_class` and
the floor and example counts a run actually used.
`DatasetStore.record_candidate` is a plain `INSERT` against an
`AUTOINCREMENT` primary key - there is no `UPDATE`/`DELETE` path onto this
table anywhere in this codebase, so "the previously active version is
retained, never overwritten in place" holds structurally, not by
convention. `DatasetStore.candidates()` returns every recorded candidate's
metadata (no model blobs); `candidate_model(version)` reads one blob by
version. Keeping this inside `vision.db` rather than a new
`models/`-directory-plus-manifest was a deliberate call: it needed no new
`backup.sh`/`restore.sh`/permissions wiring at all, since `vision.db` is
already backed up and restored wholesale (`sqlite3 ... .backup`, table-agnostic) -
this task has zero `appliance/ops/` changes as a direct result.

**`cs71vision.runtime.TrainingJob` runs a trigger, not a schedule.**
`trigger()` starts one training run on its own thread and returns
immediately; a second `trigger()` while one is in flight returns `False`
rather than stacking a second pass behind the first, since the dataset has
not changed since the in-flight run started reading it. This is deliberately
scoped to the mechanism, not the policy: PI-VISION-005 is what decides *when*
to call `trigger()` (the operator-facing `vision.train` capability), and this
task builds nothing that calls it - wiring an HTTP trigger before the
capability that is supposed to gate it exists would let any caller train
before RBAC does. `build_training_job` shares `build_correlation_loop`'s and
`build_api_server`'s own gate on `daemon_service_token_path` for a different
reason that lands on the same answer: without a token, correlation never
runs, so `vision.db` never receives an example in the first place and there
is structurally nothing to ever train from. A dedicated test proves the
"never blocks" requirement directly rather than by inspection: a blocking
fake trainer is left mid-run on its own thread while the main test thread
reads the store and gets an answer immediately, because the store's lock is
only ever held for the brief read that produces `examples()`, never across
the training computation itself.

What remains genuinely hardware-gated, and is not claimed here: this task's
own backlog entry says it plainly - "Pi 5's real training throughput gets
measured, not assumed" - and nothing in this repository runs on a Pi 5.
`RandomForestClassifier`'s wall-clock training time on real captured frames,
at real dataset sizes, on real Pi 5 CPU, is unmeasured; every test here runs
against small, fast, synthetic solid-color images on ordinary CI hardware.
This is the same class of gap `V4L2Camera`'s real-hardware behavior and the
DTR gate already are for this workspace, not a new kind of one.

- [x] A class below the configured minimum-example floor is excluded from the training set outright, not merely flagged in the UI.
- [x] Training runs as a background job that never blocks `cs71-vision`'s live suggestion path or `cs71d`'s admission path.
- [x] A trained candidate is stored as a new durable, versioned model; the previously active version is retained, never overwritten in place.
- [x] Per-class accuracy on a held-out split is recorded and retrievable before the candidate is ever offered for activation.

### PI-VISION-005 — Operator train/activate capability and RBAC extension

**Goal:** give `operator` a bounded, auditable way to retrain and activate a model, with rollback. **Implementation notes:** new `vision.train` capability, granted to `operator` and `administrator`, refused to `viewer` — a deliberate departure from reserving impactful actions to `administrator`, recorded as such in ADR-0013. **Dependencies:** PI-VISION-004, PI-WEB-002. **Hardware required:** No. **Size:** M.

Delivered for the evidence class achievable without a Pi or real camera.
`cs71vision.dataset` gained a third table (schema version 3, `activations`,
`version INTEGER NOT NULL REFERENCES models(version)`): an append-only
event log, the same discipline `web_audit` already uses for the web side.
`DatasetStore.activate(version)` inserts a row rather than mutating one -
"the previously active version is retained, never overwritten in place"
holds structurally, since there is no `UPDATE`/`DELETE` path onto that
table anywhere in this codebase, the same as `models`. "Roll back" is not a
distinct storage operation: it is a caller activating whatever version
`DatasetStore.activations()`'s second-to-last row names, and
`DatasetStore.active_version()` is simply the latest row's version - three
tiny reads and one insert cover the whole feature.

`cs71vision.api.VisionApiServer` (renamed from `DatasetApiServer`, since it
now serves more than the dataset) gained four routes on the same socket:
`GET /v1/models` (every candidate's metadata plus the active version and
whether a rollback currently has anything to go back to - computed
server-side from the same `activations()` history `_rollback_body` itself
checks, rather than left for a caller to approximate from candidate count,
which a model trained several times but only ever activated once would get
wrong), `POST /v1/train` (triggers `TrainingJob.trigger()`, PI-VISION-004's
own trigger-not-scheduler design - this is the first caller it ever gets),
`POST /v1/models/{version}/activate`, and `POST /v1/rollback`. None of the
four carry an actor or attribution: `cs71-vision` has no concept of "which
operator" and never will for this surface - that lives entirely on the
`cs71-web` side (`recordAudit`), the same way `cs71d` never learns a
browser identity either. Constructing a `VisionApiServer` now also
constructs a `TrainingJob` (its own independent `DatasetStore` connection,
per PI-VISION-003's own reasoning for why that is safe); a `Trainable`
`Protocol` inside `api.py` describes what it needs from that job rather than
importing `TrainingJob` concretely, since `runtime.py` builds a
`VisionApiServer` and a concrete import would have been circular.

**Web-side scope stayed proportionate to what PI-VISION-003 already
decided**, not re-litigated per action: `appliance/web/src/lib/server/vision/client.ts`
gained `models()`/`train()`/`activate()`/`rollback()` methods on the same
hand-typed client, reusing the same transport/credential infrastructure. Its
error mapping did grow, though - PI-VISION-003's dataset read had no
per-code distinction worth making (every failure meant the same thing,
"the numbers could not be shown"), but training/activating/rolling back are
real actions with distinct, actionable refusal reasons (a version not on
record, a rollback with nothing to roll back to), so `vision/errors.ts`
grew a per-code `SAFE_RESPONSES` map the same shape `daemon/errors.ts`
already uses, once there was something meaningful to say per code.

The `/dataset` page (`routes/dataset/`) gained a models section: every
candidate, newest first, each one's per-class accuracy shown in a table
next to the currently active model's own - the rendering itself is what
satisfies "activation is refused unless the operator has been shown the
candidate's accuracy alongside the currently active model's" (ADR-0013),
not a separate confirmation step bolted on afterward, since the activate
control for a candidate sits directly below that same comparison on the
same page. Training stays disabled until `data.dataset.trainingReady` (the
same floor PI-VISION-003 already computes); a rollback control appears only
when `cs71-vision`'s own `can_roll_back` says there is something to roll
back to. Three new form actions (`?/train`, `?/activate`, `?/rollback`)
mirror the dashboard's own `operate()` shape - `requireCapability` next to
the effect, `recordAudit` on every outcome including a refusal, a form
field validated before anything is sent - with no generation or idempotency
key, since none of these touch machine state or `cs71d`'s optimistic-
concurrency model at all.

- [x] `vision.train` is a new row in the capability matrix (`security-and-safety.md`, `appliance/web/src/lib/server/auth/capabilities.ts`), not inferred from an existing capability.
- [x] Activation is refused unless the operator has been shown the candidate's accuracy alongside the currently active model's.
- [x] Rollback to the previously active version is a supported, tested action, independent of retraining.
- [x] Every train/activate/rollback action is durably recorded in its own audit trail, mirroring `web_audit`'s pattern.

### PI-VISION-006 — Read-only classification suggestion (Phase 1)

**Goal:** surface a suggested class and confidence to the operator without ever acting on it. **Implementation notes:** the operator's actual choice, whether or not they follow the suggestion, keeps feeding PI-VISION-002's dataset loop. **Dependencies:** PI-VISION-004. **Hardware required:** Pi and camera; controller optional via the simulator. **Size:** M.

Delivered for the evidence class achievable without a Pi or real camera.
`cs71vision.classifier.classify_frame` is the primitive: unpickle the active
model, run PI-VISION-004's own `extract_features` on one frame, and read
`predict_proba` for the top class and its confidence - the same trust
boundary `training.py` already accepts for `pickle` (this process's own
output, never anything else). `cs71vision.classifier.FrameSuggester` is the
one thing `cs71vision.runtime.SuggestionLoop` calls each tick: no active
model or no captured frame yet both mean "nothing to suggest", not an error.
The loop is a fourth independent `DatasetStore` connection (same reasoning
PI-VISION-003 established for the api server's own), on the same
`build_*(config)`-returns-`None`-without-a-token gate every other
`vision.db`-backed component already uses - without a token, correlation
never runs, so no example is ever recorded and no model is ever trained,
meaning there is structurally never an active model to suggest from.

**Storage: two more append-only tables, not a mutation of an existing
one.** Schema version 4 adds `suggestions` (one row per classification) and
`suggestion_outcomes` (`suggestion_id`/`operation_id` both `UNIQUE`, so a
suggestion is matched at most once). `cs71vision.correlator.Correlator`
gained exactly one more step in its existing per-success loop: after
recording a new example, it also asks
`DatasetStore.unmatched_suggestion_at_or_before(created_at)` - the same
"newest evidence at or before this moment, `None` means discard, never
guess" contract `FrameBuffer.closest_at_or_before` already uses for frames -
and if one exists, records whether it matched. This is the same correlation
pass PI-VISION-002 already runs on the same cadence; nothing new polls
`cs71d`.

`cs71vision.api.VisionApiServer` gained two pure-read routes:
`GET /v1/suggestion` (the most recently recorded suggestion, or `null`) and
`GET /v1/suggestion-accuracy` (`total`/`correct`/`accuracy`, the last
`null` rather than a fabricated zero before anything is matched). Neither
route ever classifies on demand or writes anything - `SuggestionLoop` is
the only writer, keeping "cs71-vision never issues a `cs71d` command at
this stage, under any configuration" true by construction: nothing in this
request path calls the daemon client at all.

**Web side:** the dashboard (`/`) shows the current suggestion informationally,
directly above the existing manual sort form - the same page, not a new
one, since "before the operator picks a slot" means where they are already
looking. A `cs71-vision` outage is caught and logged, never surfaced as a
page error: sorting does not depend on `cs71-vision` (ADR-0013), and the
dashboard's own daemon-read failure handling is untouched by it, proven by
an end-to-end test that closes the fake vision socket and checks the
machine state renders normally regardless. `/dataset` gained a third,
visibly distinct accuracy figure - live suggestion accuracy - next to the
per-candidate held-out accuracy table PI-VISION-005 already renders, since
the two answer different questions ("does this model work in practice" is
not "how did it score on a held-out split of its own training data").

What remains genuinely hardware-gated: same as PI-VISION-001/004 before it,
nothing here runs against a real camera or a real trained-on-real-frames
model. Every suggestion in this test suite is a classification of small,
fast synthetic solid-color images, on ordinary CI hardware.

- [x] A suggested class and confidence is shown before the operator picks a slot.
- [x] `cs71-vision` never issues a `cs71d` command at this stage, under any configuration.
- [x] Live suggestion accuracy (suggestion versus the operator's actual choice) is tracked as its own evidence stream, separate from training-time held-out accuracy.

### PI-VISION-007 — New `cs71d` machine actor kind for autonomous sort

**Goal:** extend the daemon's contract with a narrowly-scoped, non-human actor kind restricted to `sort`. **Implementation notes:** additive OpenAPI/`API_ROLES`/`Actor` change, reviewed with the same weight as `cs71d`'s existing safety primitives; own service credential, following the two-service-token-file pattern from PI-OPS-001. **Dependencies:** PI-API-001, PI-DOMAIN-004. **Hardware required:** No — this is a daemon-contract change, evidenced the same way any other domain change is. **Size:** L.

Before this task, `cs71d`'s bearer authentication was identity-blind: one
shared secret was compared against every caller, so nothing distinguished
"this really came from `cs71-vision`" from "`cs71-web` wrote `role:
"machine"` into a request body it constructed itself" - the backlog's "own
service credential file, distinct from `cs71-web`'s and `cs71d`'s own" was
pointing at exactly this gap. `cs71d.api.ServiceIdentity` (`BFF`/`MACHINE`)
closes it: `authenticate()` now checks the presented bearer token against up
to two distinct configured secrets and returns which one matched, and
`_commanding_actor()` requires the claimed `role` and the authenticated
`identity` to agree in both directions -
`(role == MACHINE_ROLE) != (identity is ServiceIdentity.MACHINE)` is
`FORBIDDEN`. That symmetry matters: it is not enough that the machine
credential can't claim a human role, since a human-attributed request must
also be unable to claim the machine role by self-declaration alone. A
second, narrower gate is new too - the first per-role, per-command
restriction anywhere in `cs71d` (`domain.py` never inspected `actor.role`
before this) - `command != "sort"` is `FORBIDDEN` for the machine role
specifically, leaving every human role governed by the pre-existing flat
`COMMANDING_ROLES` gate plus SvelteKit's own RBAC, unchanged.

**Credential provisioning follows a deliberately different shape from the
existing three-file pattern.** `write_service_tokens` (`appliance/ops/lib/common.sh`)
writes one shared secret to three files, one per service. The new
`write_machine_service_token` writes a second, *distinct* secret to only
two files - `/etc/cs71d/machine-service-token` and
`/etc/cs71-vision/machine-service-token` - never `/etc/cs71-web/`, since
`cs71-web` has no legitimate reason to ever act as the machine identity; a
test asserts the string `cs71-web` does not appear in that function's body
at all. `DaemonConfig.machine_service_token_path` stays optional even in
production (unlike `service_token_path`, which is effectively required to
serve) - the capability can exist, provisioned, for a long time before
PI-VISION-008 ever configures `cs71-vision` to present it; `None` leaves the
machine role structurally unreachable regardless of request content, proven
by a real end-to-end `Daemon` + socket test.

**No schema migration, on either side of the boundary.** The contract's
`Actor.role` enum gained a fourth value; the `FROZEN_ENUMS` contract guard
only checks top-level enum schemas by strict equality, not enums nested on
an object property, so this additive change was correctly left unflagged -
confirmed by reading the guard's own assertion rather than assumed.
`operations.Actor` was already an unconstrained `{user_id, role}` dataclass
and `journal.py`'s `operations` table already stores `actor_role` as
untyped `TEXT`, so a machine-attributed operation is durably journaled and
distinguishable from a person's decision with zero changes to either. Only
`cs71d-v1.d.ts` needed mechanical regeneration; `cs71-web`'s own `Role` type
(`viewer`/`operator`/`administrator`) is a separate, closed vocabulary that
cannot express `role: "machine"` in any payload it constructs, so no
web-workspace logic changed.

- [x] The new actor kind cannot reach `machine.recover`, `config.write` or `users.manage` under any circumstance, enforced at the daemon boundary, not only the UI.
- [x] It authenticates with its own service credential file, distinct from `cs71-web`'s and `cs71d`'s own.
- [x] Every command it submits is durably journaled and distinguishable in the audit trail as machine-attributed, never presented as a person's decision.
- [x] The contract change is additive; no existing `FROZEN_*` contract guard breaks.

### PI-VISION-008 — Confidence-gated hybrid autonomy (Phase 2)

**Goal:** let a sufficiently confident classification submit an autonomous sort through the new actor kind; hold everything else for operator confirmation. **Implementation notes:** per-class threshold, conservative default. **Dependencies:** PI-VISION-006, PI-VISION-007, PI-VISION-010. **Hardware required:** Pi, camera and controller for real autonomous motion; routing/threshold logic itself is evidenced via simulator first. **Size:** L.

- The confidence threshold is configurable per class and defaults conservative (mostly manual).
- A below-threshold case is held for explicit operator confirmation; it is never silently skipped or defaulted to a guess.
- A false-autonomous-sort rate is measured and recorded per class before that class's threshold may be lowered.
- A primer-presence flag (PI-VISION-010) always overrides autonomy regardless of manufacturer-class confidence.

### PI-VISION-009 — Configurable routing profiles

**Goal:** let an operator choose how manufacturer classes map onto 8–10 physical chutes, per run. **Implementation notes:** fixed map plus overflow, dynamic per-batch assignment, and two-pass coarse-then-fine, all as `cs71-vision` configuration rather than one hardcoded strategy (ADR-0013). **Dependencies:** PI-VISION-006. **Hardware required:** Pi and camera for a real run; profile-selection logic itself is software-evidenced. **Size:** L.

- An operator selects a routing profile before a run starts; the active profile is visible throughout the run.
- Dynamic per-batch mode shows a live, accurate chute↔class legend, updated the moment a new class is first seen in that run.
- Fixed-map mode supports pre-assigning specific classes to specific chutes with exactly one defined overflow chute.
- Two-pass mode's second pass operates against exactly one prior group's output, never the whole batch.

### PI-VISION-010 — Primer-presence axis with mandatory confirmation

**Goal:** treat primer presence as a permanently separate signal that always requires operator confirmation. **Implementation notes:** no configuration surface, including administrator-level ones, may disable this. **Dependencies:** PI-VISION-006. **Hardware required:** Pi and camera for the software wiring; real primer-bearing cases and physical trial evidence, reviewed with the same weight as DTR/HIL evidence, before this is ever trusted in practice — never closed on software evidence alone. **Size:** M.

- A primer-flagged or ambiguous case always routes to operator confirmation, regardless of manufacturer-class confidence or the autonomy configuration in PI-VISION-008.
- No code path, configuration flag or role can bypass this — verified the same way `protocol.direct` is verified as unreachable by any role today.
- This is explicitly documented as never closing on software evidence alone, matching SAF-07's DTR posture.

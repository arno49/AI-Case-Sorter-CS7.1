# Repository Agent Guide

This file applies to the entire repository. More specific instructions may add
constraints for a subtree, but they must not weaken the protocol, safety, or
hardware-evidence boundaries below.

## Repository map

| Path | Purpose |
| --- | --- |
| `ArduinoCode/CS71_Arduino/` | Canonical Arduino Uno firmware |
| `ArduinoCode/PROTOCOL.md` | Byte-exact legacy v1 contract |
| `ArduinoCode/PROTOCOL_V2.md` | Normative opt-in v2 contract |
| `ArduinoCode/PROTOCOL_V2_PLAN.md` | Firmware/host delivery status and hardware gates |
| `test/` | PlatformIO native firmware tests and fixtures |
| `host/` | Python `cs71_protocol` library and `cs71-protocol` CLI |
| `appliance/contracts/` | Executable private `cs71d` OpenAPI contract and compatibility tests |
| `appliance/daemon/` | Python `cs71d` workspace; sole serial owner |
| `appliance/daemon/src/cs71d/simulator/` | Deterministic no-hardware protocol simulator |
| `appliance/web/` | SvelteKit SSR/Node.js browser-facing BFF workspace |
| `docs/architecture/` | Canonical Raspberry Pi appliance architecture, ADRs, roadmap, and backlog |
| `RASPBERRY_PI_WEB_ARCHITECTURE.md` | Raspberry Pi architecture executive summary |
| `3DModels/` | Canonical printable mechanical parts |
| `Mods/` | Optional approved modifications |
| `CommunityContributions/` | Independent variants; not canonical by default |

The Raspberry Pi application currently has approved architecture, an executable
API contract, initial daemon/web workspace scaffolds, a single-owner serial
worker, published session state with conservative reconnect, and durable
operations with idempotent admission, fail-closed durability, an attributable
priority stop, capability-validated home and sort adapters, a socket-only
internal API with bounded resumable events, local web authentication with
opaque server-side sessions, server-side role enforcement against the documented
capability matrix, CSRF, origin, rate and concurrency controls on
state-changing requests, a socket-only daemon client behind a dashboard that
reads the machine snapshot and submits the software stop, and a browser event
stream fanned out from one daemon reader whose consumers re-read a snapshot
rather than present a gap. The dashboard's wording is decided in one tested
module: acceptance never reads as completion, an unconfirmed terminal reads as
an outcome that is not known, an unobserved axis reads "not known" rather than
"not homed", and the software stop is the first control in the tab order.
Manual connect, home, sort and feed controls are offered capability-driven from
the snapshot the operator saw; every command form carries that snapshot's
generation and a render-minted idempotency key, and the feed control stays
disabled with the daemon's reason for the unqualified firmware gate shown
verbatim. The operation history screen at `/operations` is implemented,
reusing the dashboard's own per-operation wording so a row is never worded as
a completion; the fault, recovery and system views (PI-UI-002) are not
implemented, and neither is deployed or qualified.

## Current validated baseline

- Firmware version: `7.1.260714.6`.
- Reset and the default `uno` build remain legacy protocol v1.
- `uno_v2` compiles v2 support but still starts in v1 and requires explicit
  negotiation.
- `native`: 89 passing tests.
- `native_v2`: 49 passing tests.
- Host package: 116 passing pytest tests.
- `cs71d` daemon package: 298 passing pytest tests.
- `appliance/web` workspace: 472 passing vitest tests.
- `uno`: 17,594 bytes flash, 899 bytes static SRAM.
- `uno_v2`: 26,290 bytes flash, 997 bytes static SRAM.

These numbers describe the current software baseline, not hardware
qualification. Update the relevant README and plan evidence when a change
legitimately changes them.

## Existing validation commands

From the repository root:

```sh
pio run -e uno
pio run -e uno_v2
pio test -e native
pio test -e native_v2
```

Host package:

```sh
cd host
python -m pip install ".[dev]" "build==1.2.2.post1"
python -m pytest
python -m build --wheel --sdist
```

Raspberry Pi daemon contract:

```sh
python -m pip install --require-hashes -r appliance/contracts/requirements.txt
openapi-spec-validator appliance/contracts/cs71d-v1.openapi.json
python -m unittest discover -s appliance/contracts/tests -v
```

Raspberry Pi application workspaces:

```sh
python -m pip install --require-hashes -r appliance/daemon/requirements-dev.txt
python -m pip install --no-build-isolation -e ./host -e ./appliance/daemon
(cd appliance/daemon && ruff format --check . && ruff check . && mypy && pytest)
python -m build --no-isolation --wheel --sdist appliance/daemon
(cd appliance/web && npm ci && npm run check:api && npm run lint && npm run check && npm test && npm run build)
```

Use the smallest existing command that covers a change, then run all affected
environments before declaring the task complete. Do not add new build or lint
systems merely to validate a documentation or surgical code change.

## Firmware and protocol invariants

- Preserve byte-for-byte v1 behavior unless a separately approved compatibility
  change explicitly says otherwise. The existing Windows application depends
  on exact spellings, leading spaces, line ordering, and response timing.
- Reset must select v1. Protocol mode, request state, and CRC mode are volatile.
- V2 is an opt-in evolution of familiar v1 payloads. Keep ASCII LF/CRLF framing,
  the 64-byte v2 limit, correlated lifecycle, and the exact negotiation
  boundaries in `PROTOCOL_V2.md`.
- Exact ID-less `stop` is the priority recovery command. It must remain
  recognizable before ordinary framing and optional CRC.
- Never represent timeout, malformed/interleaved input, missing terminal, CRC
  uncertainty, transport loss, or failed recovery as success.
- Reuse shared validation and command handlers. Do not create divergent v1/v2
  motor-control implementations.
- Keep motion cooperative and serial-responsive. Do not add millisecond
  `delay()` calls to runtime paths.
- Avoid Arduino `String`, dynamic allocation, and unnecessary SRAM-backed
  literals. Use `F("...")` for fixed serial text where appropriate.
- Start a scope review before V2-11 if `uno_v2` exceeds the documented
  29,000-byte flash or 1,250-byte static SRAM thresholds; do not normalize the
  increase or revise the thresholds without an approved plan.
- The firmware CRC core is currently dormant and excluded from production
  builds. Do not integrate CRC wire transitions before the V2-08H/V2-09
  dependency gates are legitimately closed.

## Host library invariants

- `host/src/cs71_protocol/` is the tested Python protocol boundary. Reuse it
  instead of reimplementing framing or recovery in application code.
- Never trim protocol lines with `.strip()`; the v1 ping response has a
  significant leading space.
- Reads and operations require finite deadlines. Post-transmission uncertainty
  must clear correlation state and use fail-closed recovery.
- Keep protocol `request_id`, daemon `operation_id`, daemon event IDs, and
  snapshot generation conceptually and structurally separate.
- Real serial DTR guarantees are platform-specific. The existing CLI does not
  claim safe pre-open DTR suppression on Linux/macOS.

## Raspberry Pi appliance decisions

Accepted decisions are recorded in `docs/architecture/adr/`.

- SvelteKit SSR is the browser-facing UI/BFF.
- Python `cs71d` is the sole serial owner and survives Node/web restarts.
- SvelteKit calls `cs71d` through internal HTTP/JSON over a Unix domain socket.
- Commands use REST; updates use bounded resumable SSE. WebSocket is not part of
  the MVP.
- Browser code never reaches the daemon, serial device, or arbitrary protocol
  commands directly.
- `cs71d` and SvelteKit own separate SQLite databases and never share writes.
- Native Raspberry Pi OS deployment uses systemd, udev, and Caddy; containers
  are excluded from the MVP.
- Only `appliance/daemon/src/cs71d/serial_worker.py` may import or construct
  `ProtocolClient`. Only `appliance/daemon/src/cs71d/device.py` may reference
  `SerialTransport`, and it must stay a lazy import so `import cs71d` never
  requires pyserial. Other daemon code submits typed intents to `SerialWorker`
  and never performs serial I/O itself.
- Do not reimplement v1/v2/CRC recovery in the daemon. `cs71_protocol` already
  runs stop/reset/verify for an unsafe exchange and reports the outcome as
  `RecoveryError.recovered`; the daemon only chooses re-activation, reconnect,
  or `UNCERTAIN`.
- Keep daemon connection state separate from worker-thread lifecycle state. A
  running thread may hold a `RECOVERING` or `UNCERTAIN` session, and only a
  `READY` session admits work.
- Only `appliance/daemon/src/cs71d/journal.py` may import `sqlite3` or open
  `machine.db`. Other daemon code passes typed records and never writes SQL.
- A durable record is committed before the state it describes is reported:
  admission, every lifecycle transition and every terminal outcome are journal
  writes. `operation_transitions` is append-only, and `SUCCEEDED` requires a
  trusted correlated firmware terminal — enforced by the operation model and
  independently by a database trigger.
- Operation lifecycle is `QUEUED → ACCEPTED → RUNNING → {SUCCEEDED, FAILED,
  CANCELLED, UNCERTAIN}`. `SUCCEEDED` is reachable only from `RUNNING`, because
  a command that was never transmitted cannot have a firmware terminal.
- Journal migrations are forward-only and checksummed. A newer or diverged
  schema refuses to open rather than downgrading in place.
- The snapshot generation belongs to the machine view, not to the serial
  session. `SessionState` contributes connection confidence into
  `MachineState`; it does not own the version callers compare against.
- Admission evaluates the idempotency key, the observed generation and
  readiness while the machine view is held still, and journals the operation
  before anything is enqueued. A stale or duplicate request must never reach
  the controller.
- Lock order is `MachineState` then `Journal` then `SerialWorker`, never the
  reverse. An admitting thread releases the machine lock before enqueueing,
  because the worker thread enters that lock while holding nothing.
- `SUCCEEDED` always requires a trusted terminal, and what counts as one is
  decided per operation class: a correlated firmware terminal for machine
  motion, a verified session with its required snapshots for connect and
  recover, and the committed snapshot for a configuration change. No operation
  may succeed on the strength of having been asked.
- A command that reached the wire without a trusted terminal is `UNCERTAIN`,
  never `FAILED`: the daemon does not know whether the machine moved.
- A refused journal write latches the machine as undurable and blocks new
  motion with `JOURNAL_UNAVAILABLE`. It does not self-clear; durability loss
  needs operator or service intervention. Never substitute an in-memory claim
  of success for a durable record.
- The daemon has no TCP code path. `cs71d.api` binds `AF_UNIX` only, with
  owner/group-only socket permissions, and every request carries the
  installation-local bearer service credential. Do not add an internet address
  family, a port, or an unauthenticated route.
- The service credential is read from a protected file named by
  `service_token_path`. Never accept it as a configuration value or a
  command-line argument, and never serve when other users can read the file.
- Starting the daemon must not displace one that is already serving the socket:
  a second instance would take the path while the first still owns the serial
  port.
- Only `appliance/web/src/lib/server/auth/database.ts` may import
  `better-sqlite3`, open `web.db` or write SQL, and only
  `appliance/web/src/lib/server/auth/passwords.ts` may import `@node-rs/argon2`.
  The whole authentication core lives under `$lib/server`, which SvelteKit
  never bundles into client code.
- `web.db` is opened owner-only and refused when another local identity can
  already read it. Its migrations are forward-only and checksummed, and a
  newer or diverged schema refuses to open rather than downgrading in place.
- No default account or default password ships. The first administrator is
  created by claiming a one-time, expiry-bound bootstrap token, and issuing a
  new token supersedes any outstanding one so only a single bootstrap
  credential is ever live. Provisioning cannot be re-opened once claimed.
- Passwords are stored only as Argon2id encodings under the policy in
  `passwords.ts`, enforced by a database `CHECK`. Session and bootstrap tokens
  are 256-bit random values stored only as SHA-256 digests, so a stolen
  `web.db` yields no usable credential.
- Sessions are opaque and server-side. Logging in issues a new session and
  revokes the one it replaces; logout, idle expiry, absolute expiry, account
  disable and password change all revoke. Revocation is final and a session
  may not be rebound to another account or token, both enforced by triggers.
- `hooks.server.ts` denies by default in both decisions it makes: a route is
  reachable without a session only if its policy says `public`, and a signed-in
  account reaches it only if the policy names a capability the role holds, so a
  new page is protected by omission. A request presenting a token the server
  will not honour always leaves without that cookie. The login redirect carries
  a fixed reason code and never a caller-supplied return path.
- The RBAC matrix in `docs/architecture/security-and-safety.md` has exactly one
  implementation, `capabilities.ts`. Routes ask for a capability, never for a
  role; do not compare a role name in a handler. A viewer may stop the machine,
  and no role at all may drive the protocol or the device path.
- Every route declares its access in `policy.ts`, keyed by route id, and a route
  with no entry is refused rather than resolved. `policy.spec.ts` scans the
  route directory, so a page added without an entry fails the build. A path that
  matched no route is left to answer as missing rather than as forbidden.
- Authorization is decided server-side before the effect. A handler whose action
  needs more than the page calls `requireCapability` next to the effect; what a
  page shows is a reflection of the server's decision, never a substitute for
  it.
- A state-changing request -- anything but `GET`, `HEAD` or `OPTIONS` -- must
  name the appliance's own origin and echo back the CSRF token, and is checked
  before any handler runs, cheapest check first. A missing `Origin` header is
  refused, not excused. Do not exempt a route from these checks; they apply by
  method, so a new state-changing route inherits them.
- The CSRF token is derived from the session token by HMAC rather than stored,
  so it rotates with the session and a stolen `web.db` still yields nothing. A
  browser with no session gets a random token in its own `HttpOnly` cookie,
  because signing in is state-changing too.
- Login and every other state-changing route are bounded by the documented
  budgets in `limits.ts`: 30 state changes per minute per address, five sign-in
  attempts per minute per account and per address, two password hashes in
  flight, and a 16 KiB declared form body. The concurrency limit refuses rather
  than queues -- an Argon2id hash costs 64 MiB, and queueing turns a burst into
  memory pressure.
- The session cookie carries an opaque token only -- no role, no user id, no
  signed claims. It is `HttpOnly`, `SameSite=Strict` and root-scoped in every
  profile, and `Secure` with the `__Host-` prefix in production. Ending a
  session is a POST; a `GET` sign-out would let any page sign an operator out
  of a running machine.
- `appliance/contracts/cs71d-v1.openapi.json` is the source of truth for the
  API surface. Translate daemon vocabulary at that boundary; never let protocol
  internals, raw serial content or secrets appear in a response body.
- `appliance/web/src/lib/server/daemon/` is the only way the web workspace
  speaks to `cs71d`. It exposes named commands with typed arguments and no
  method that takes a path, a device or a protocol string; the socket path comes
  from configuration and request paths are literals. Do not add a pass-through.
- Every daemon command carries an idempotency key, a generation to match and a
  deadline. The software stop is the only command that may match any generation,
  and it is never presented as an emergency stop. An accepted command yields an
  `operation_id` and a pending state, never a claim that the machine acted.
- Daemon errors are translated, not forwarded: the daemon's code, request id and
  words stay in the server log, and the browser gets wording this workspace
  owns. A daemon `401`/`403` is this service's credential being wrong, so it is
  reported as unavailable and never as the operator's permissions.
- The web service reads its daemon credential from the file named by
  `CS71_WEB_SERVICE_TOKEN_PATH`, never from an environment value, and refuses a
  file other local users can read.
- A route that commands the machine authorizes in the handler, next to the
  effect, and never infers permission from the fact that a page rendered a
  control. A daemon that is not answering makes a page report that rather than
  fail: the screen is what an operator has when the machine has gone quiet, and
  the stop control has to stay on it.
- The stop never reuses an idempotency key. Deduplication is for a resubmitted
  intent; a second press of stop is a second intent, and a replayed key would
  let the daemon answer with the first result and turn a stop into a no-op.
- Every machine command writes a `web_audit` row in `web.db` whether it was
  accepted, refused or never answered. It is attribution, not the machine's
  journal of record: `cs71d` owns what the machine did, the two databases are
  never written in one transaction, and `operation_id` is the join key. Audit
  rows cannot be edited and the table has no column for a credential or a form
  body.
- The event stream is the only thing the web workspace reconnects to on its own.
  Reading may be retried; a command may not, because one that timed out may have
  moved the machine. Every reconnection announces a `resync` so a consumer
  re-reads a snapshot rather than applying increments across a gap, and a cursor
  the daemon has rejected is forgotten rather than presented again.
- Daemon `event_id`, `operation_id` and `generation` reach the browser
  unrenumbered. A bridge with its own sequence would make the two ends
  impossible to line up. An unknown event type is passed through; ignoring it is
  the consumer's decision under the v1 contract.
- One process, one reader. `EventBroadcast` attaches to the daemon's stream when
  the first browser subscribes and lets go when the last one leaves; a second
  connection would buy nothing and would spend the daemon's retention budget on
  an identical stream. A browser never applies back pressure: one that falls
  behind has its backlog discarded and is told to read a snapshot, because a
  viewer that far out of date cannot be caught up by more events. Nothing in the
  fan-out ever waits for a browser, so a browser that stops reading or vanishes
  cannot slow daemon event production or the serial worker behind it.
- Only real events carry an SSE `id:`. A resynchronisation or unavailability
  notice must not become a cursor, or a browser's next `Last-Event-ID` would name
  a position that never existed. A cursor this process cannot resume — absent,
  unparseable, older than what it retained, or from before a restart — is
  answered with a resynchronisation rather than a guess.
- The browser builds no machine state out of an event. An event says the machine
  moved past the generation on screen; the answer is to read a snapshot, which is
  the only thing that describes the machine completely. Reads are coalesced so a
  busy machine cannot make a page flood itself, and a screen that owes a snapshot
  says so rather than looking current.
- What a screen says about the machine is decided in
  `appliance/web/src/lib/machine-status.ts`, not in markup. No word of
  completion describes an operation that has not settled, and settled is reached
  only through a terminal with `trusted_terminal` — an unconfirmed terminal is
  presented as an outcome that is not known, never repeated by its state name.
  An axis the session has not observed reads "not known", not "not homed": the
  wire has no third value, and a guess in the safe-looking direction is still a
  guess. `UNCERTAIN` keeps a tone of its own so it cannot collapse into ordinary
  attention.
- The software stop is the first control in the document and therefore in the
  tab order; no element on an operator page declares a positive `tabindex`. The
  page says beside the button that it is a software stop, not an emergency stop.
- A manual command form is an intent, not a button press. Which of connect,
  home, sort and feed may be offered is decided in
  `appliance/web/src/lib/machine-controls.ts` from the snapshot the operator is
  looking at — never offered without a snapshot, motion never offered without a
  `READY` session or against an unadvertised capability, the slot list exactly
  `slot_count` long, and feed following `feed_available` with the daemon's
  `feed_unavailable_reason` shown verbatim while the firmware gate is
  unqualified. The form carries the snapshot generation and an idempotency key
  minted by the server load for that render, so a stale page is refused by the
  daemon and a resubmitted form deduplicates instead of moving the machine
  twice. Withholding a control in the page is a courtesy; authorization and
  admission happen again on the server and in the daemon.
- The operation history screen at `/operations` (`appliance/web/src/lib/operation-history.ts`)
  reuses `machine-status.ts`'s `operationReading` for every row rather than
  reimplementing its wording, so the two screens cannot describe the same
  operation two different ways. Its filters offer exactly the `state`/`type`
  values the contract defines, a filter value this workspace does not
  recognise is dropped rather than forwarded to the daemon, and paging
  forward uses the daemon's own opaque `next_cursor`. It is a `GET`, not a
  form post — there is no action here to audit.
- Restarting the web service drops a database handle and a reader and nothing
  else. `cs71d` owns every operation in flight and goes on running it, which is
  why a restart can neither cancel nor duplicate one, and why no command is ever
  resent on the way back up.
- SQL lives only in `appliance/web/src/lib/server/auth/` and
  `appliance/web/src/lib/server/audit.ts`. Adding a module to that list is a
  reviewed change; `boundaries.spec.ts` fails otherwise.
- Validate a command against what the controller advertised, not against what
  the daemon assumes. The worker gathers capabilities and status before
  publishing `READY` and re-observes them after each completed movement;
  capability, gate and readiness checks run before any serial I/O.
- The v2 feed lifecycle gate `FEED_LIFECYCLE_GATE` is `NOT_EXECUTED`. Feed
  returns `UNSUPPORTED` and no simulator run may close that gate.
- Priority stop is a durable attributable operation that skips the readiness
  check ordinary motion must pass and may be requested against a stale or
  uncertain view. Without its trusted exact `stopped` terminal, the stop and
  the work it affected are `UNCERTAIN`, never stopped-successful. It is still
  a software stop, not an E-stop.
- Simulator code uses explicit clock advancement, carries a conspicuous
  `SIMULATOR_ONLY` identity, and never upgrades simulator results into hardware
  evidence.
- New architecture decisions require a new or superseding ADR. Keep
  `roadmap.md`, `backlog.md`, and `traceability.md` synchronized.

## Hardware and safety evidence

- Simulator/native tests never satisfy DTR, USB electrical behavior, physical
  motion/completion, stop latency, sensor, HIL, Windows, or production-release
  criteria.
- Linux/Raspberry Pi DTR behavior remains `NOT_EXECUTED` and unqualified.
- Software `stop` is not a physical emergency stop. Do not label or rely on it
  as one.
- Do not check a hardware acceptance box without representative hardware,
  recorded versions/configuration, raw observations, and explicit pass/fail
  evidence.
- If hardware is unavailable, implement and validate only the software portion
  and leave the physical gate visibly blocked.

## Change discipline

- Make canonical firmware fixes only in `ArduinoCode/CS71_Arduino/` unless the
  task explicitly targets a community variant.
- Do not propagate contributor-specific pins, mechanics, or board assumptions
  into canonical code.
- Update documentation and fixtures when behavior, resources, commands,
  architecture decisions, or qualification status change. The required files
  are listed under "Ways of working" below; do not infer the set from what an
  earlier commit happened to touch.
- Keep changes narrowly scoped; do not rewrite unrelated user changes or
  silently tighten legacy behavior.

## Ways of working

These are requirements, not suggestions. A task is not complete until every
applicable item is satisfied.

### Before implementing

- Read the current source of any library you are about to build on, and design
  against what it actually does. `cs71_protocol` in particular already owns
  v1/v2/CRC recovery, correlation, and stop semantics; wrapping it is correct,
  duplicating it is not.
- Verify an assumption about behavior by running it, not by inference. Probing
  a simulator scenario before writing assertions is cheaper than debugging a
  test that encodes a wrong belief.
- Confirm the backlog entry's acceptance criteria and dependencies, and check
  the architecture documents for the canonical model before inventing one.
  `backlog.md` may state a subset; `docs/architecture/` is canonical.

### Finishing a task

Update every file below that the change touches. Missing one leaves the
repository describing a state that no longer exists:

| File | Update when |
| --- | --- |
| `docs/architecture/backlog.md` | A task's status or acceptance evidence changes |
| `docs/architecture/roadmap.md` | Implementation status or the active critical-path step changes |
| `docs/architecture/traceability.md` | The requirement-to-task or evidence mapping changes |
| `issues.md` | An appliance checklist item closes, or a recorded test count changes |
| `README.md` | Component status or a published test/resource count changes |
| `AGENTS.md` | An invariant, boundary, repository-map entry, or baseline number changes |
| Workspace `README.md` | The workspace gains or changes a capability |

- Run the smallest gate set that covers the change, then every affected
  workspace's full gate set before declaring completion. For daemon work that
  is `ruff format --check`, `ruff check`, `mypy`, and `pytest`.
- Report gate results as observed. If a gate cannot run locally, say which one
  and why rather than omitting it.

### Tests

- Tests that observe a worker thread must await a published transition through
  an observer or event. Do not sleep, poll, or rely on a future's completion to
  imply that the thread finished its follow-up work; the worker resolves a
  caller's future before it finishes recovering.
- Never advance simulated time with a wall-clock sleep. Use explicit clock
  advancement.
- Re-run a suite containing concurrency tests several times before declaring it
  stable.
- State the evidence class. Simulator results are labelled and never presented
  as hardware evidence.

### Branch and PR flow

- Branch from an up-to-date `main`; never commit directly to `main`.
- One reviewable change per PR. Land documentation for a change with that
  change, not afterwards.
- The PR body states what changed, how each acceptance criterion is met, the
  evidence class, and any deliberately deferred work.
- Wait for CI to pass before merging. Merge with a merge commit, then delete
  the branch locally and on the remote.
- Root-level `*.md` changes do not match the CI path filters, so a docs-only PR
  legitimately reports no checks. Confirm that is the reason rather than
  assuming a broken workflow.

# cs71d

`cs71d` is the private machine-control daemon for the Raspberry Pi appliance.
This workspace currently provides the package boundary, strict configuration
validation, sole serial ownership, published session state, durable operations
with idempotent admission, and an attributable priority stop. Typed operation
adapters and the Unix-socket API arrive in later roadmap tasks.

The package depends on the repository's `cs71_protocol` implementation and does
not duplicate framing or recovery logic.

## Serial worker

`cs71d.SerialWorker` owns one transport and one `ProtocolClient` on a single
dedicated thread; no other module may import `ProtocolClient`, and a static
test enforces that boundary. Callers never touch the serial device. They submit
closed typed intents — `QueryIntent`, `HomeIntent`, `SortIntent` — and receive a
future.

Admission has two lanes. The normal lane is bounded and rejects with
`QueueFullError` instead of allocating without limit; exactly one
state-changing operation is dispatched at a time. Priority stop is admitted
independently of normal-lane saturation, clears queued work, and preempts an
active request through the protocol library's interrupt polling and trusted
out-of-band stop.

Preemption results are fail-closed. Work invalidated by a stop fails with
`PreemptedByStopError`. If the stop itself does not complete trustworthily, the
affected result fails with `WorkerUncertainError` — never as stopped or
successful. Software stop is not a physical emergency stop.

## Session state and reconnect

`SerialWorker.session` publishes a `SessionSnapshot`: a `ConnectionState`, a
monotonic `generation`, and the reason for the transition. Connection state is
*session confidence* and is deliberately separate from `WorkerState`, which is
the owning thread's lifecycle — a running thread can hold a `RECOVERING` or
`UNCERTAIN` session, and only a `READY` session admits new work. Bootstrap
walks `DISCONNECTED → CONNECTING → VERIFYING_V1 → ACTIVATING_V2 → READY`, and
every material transition increments the generation exactly once.

In-session recovery belongs to `cs71_protocol`. An unsafe exchange has already
run its exact stop/reset/verify sequence before the worker sees it, and reports
the result through `RecoveryError.recovered`. The worker re-activates v2 when
v1 was verified; otherwise it escalates to a full reconnect that closes the old
transport and restarts from `DISCONNECTED`. When neither succeeds the session
becomes `UNCERTAIN` and stops admitting work. Verified recovery and failed
recovery therefore stay distinguishable by state, never collapsed into success.

Recovery never replays an incomplete state-changing command. Queued work is
discarded with `PreemptedByRecoveryError` rather than carried across the break,
because the snapshot generation has moved and callers must re-admit against the
new generation.

## Operations and the durable journal

`cs71d.operations` is pure vocabulary: a UUID `operation_id`, restricted actor
attribution, a finite deadline, the lifecycle `QUEUED → ACCEPTED → RUNNING →
{SUCCEEDED, FAILED, CANCELLED, UNCERTAIN}`, and the canonical request
fingerprint two requests must share to count as the same request. The
fingerprint covers the action, the validated body and the actor, so reusing one
idempotency key across different actors conflicts instead of silently sharing
an operation.

`SUCCEEDED` is reachable only from `RUNNING` and only with a trusted correlated
firmware terminal. A command that was never transmitted cannot have produced
one, so no admission or dispatch failure can present itself as success.

`cs71d.journal` owns `machine.db` — the only module that imports `sqlite3`. It
uses WAL, owner-only permissions, `synchronous = FULL`, and forward-only
checksummed migrations; a newer or diverged schema refuses to open rather than
downgrading in place. Admission writes the operation, its first transition and
its idempotency binding in one transaction, so a deduplication key can never
outlive the operation it points at. Two invariants are enforced by the storage
engine itself: `operation_transitions` rejects update and delete, and an
operation row cannot enter `succeeded` without a trusted terminal.

The protocol `request_id` is recorded only as diagnostic, session-scoped
metadata for transcript correlation. It wraps, it is not unique across
sessions, and it is never used to find, match or deduplicate an operation.

## Admission, generation and dispatch

`cs71d.MachineState` owns the snapshot generation. It is the *machine's*
version, not the session's: `SessionState` folds connection confidence into it,
and every operation transition advances the same counter, so a caller holding
generation *N* can trust that nothing material happened while it still observes
*N*.

`cs71d.OperationDomain.submit` evaluates the idempotency key, the observed
generation and readiness while holding the machine view still, and journals the
operation before anything is enqueued:

- A replayed key with an equivalent canonical request returns the original
  operation, including when the caller's generation has since gone stale — a
  retry that lost the race must deduplicate, not start a second movement.
- A key reused for a different request conflicts.
- A stale generation is rejected with no journal write, no published change and
  no serial I/O.
- Exactly one command can be admitted against one observed generation;
  concurrent callers see `STALE_GENERATION`.

Dispatch is gated on the worker thread immediately before the first byte is
written. A deadline that expired while the operation waited in the queue fails
it there, which is the last moment the command can still be stopped, and the
same gate means a lifecycle write that cannot be recorded is never transmitted.

Outcomes are fail-closed. `SUCCEEDED` requires a trusted correlated firmware
terminal; a correlated error terminal is `FAILED`; work invalidated by a stop
or discarded by recovery is `CANCELLED` and never replayed; and anything that
reached the wire without a trusted terminal is `UNCERTAIN`, because the daemon
does not know whether the machine moved.

Locks are taken as `MachineState` → `Journal` → `SerialWorker` and never the
reverse. An admitting thread releases the machine lock before it enqueues,
because the worker thread enters that lock holding nothing.

## The private API

`cs71d.ApiServer` serves the daemon's HTTP/JSON API on a Unix domain socket and
nothing else. There is no TCP code path to misconfigure: no daemon module names
an internet address family, and a static test keeps it that way. The socket is
created with owner/group-only permissions, and every request must carry the
installation-local bearer service credential the contract defines. A browser
never reaches this surface; the SvelteKit BFF does.

`appliance/contracts/cs71d-v1.openapi.json` is the source of truth. Daemon
vocabulary is translated at this boundary rather than leaking outward, error
codes map to the documented HTTP statuses through one table keyed by the domain
error code, and no response body carries protocol internals or raw serial
content.

Currently served: `/v1/health/live`, `/v1/health/ready`, `/v1/snapshot`,
`/v1/operations`, `/v1/operations/{operation_id}`, and the commands
`/v1/operations/home`, `/sort`, `/feed` and `/v1/machine/stop`.
`/v1/snapshot` returns `ETag: "generation:<n>"`, and an unobserved controller
advertises no v2 and no capability rather than a hopeful default. The session
and configuration resources and the SSE stream arrive in later roadmap tasks.

Every command carries `Idempotency-Key`, `If-Match-Generation` and
`X-Deadline-Ms`. Only a priority stop accepts `*` for the generation, because
a recovering or uncertain view is exactly when an operator needs it. Attribution
is checked against the contract's role vocabulary and a `viewer` is refused
before anything is admitted; that is defence in depth, not authority — SvelteKit
authorizes the browser identity, and the daemon never treats a supplied role as
a credential.

Responses are checked against the frozen contract schemas in tests by a
conformance checker that fails closed on any keyword it does not implement,
including the contract's conditional safety rules: a terminal operation must
carry an outcome, and only a trusted terminal may be `SUCCEEDED`.

## Operation adapters and firmware gates

The worker gathers the required snapshots — advertised capabilities and
observed status — before it publishes `READY`, and re-observes them after each
completed movement. The daemon therefore validates commands against what the
controller *reports*, not against what it assumes; a firmware build that
advertises less cannot be asked for more.

`cs71d.adapters` translates an allow-listed request into a closed worker
intent in two stages, because the two answers mean different things to a
caller. Shape and vocabulary are checked first (`VALIDATION_FAILED`), then
capability, firmware gate and readiness against the frozen admission view
(`UNSUPPORTED` or `NOT_READY`) — all before anything is enqueued:

- Home accepts only `feeder`, `sorter` or `both`, and only when the controller
  advertises the matching homing capability.
- Sort is bounded by the advertised `slot_max` and refused while the sorter
  position is unknown, because until it is homed the daemon cannot say where a
  sort would move to.
- Feed is refused outright. `FEED_LIFECYCLE_GATE` is `NOT_EXECUTED` and no
  firmware build advertises a v2 feed lifecycle, so feed returns `UNSUPPORTED`
  without touching the serial session. That gate closes with V2-09 and its
  hardware evidence; a simulator run cannot close it.

The action selects the intent and the body must contain exactly that intent's
own field, so no API or BFF input can smuggle a raw protocol payload. The
trusted terminal's fields are recorded against the operation as evidence of
what the controller actually reported.

## Durability and priority stop

A journal write that is refused latches the machine view as undurable with a
`LATCHED` fault. It stops admitting work, rejects new motion with
`JOURNAL_UNAVAILABLE`, and does not clear on its own — durability loss needs
operator or service intervention, so the daemon stays not-ready rather than
quietly resuming control on an unrecorded path.

Because the dispatch gate runs immediately before the first byte, a lifecycle
write that cannot be recorded stops the command from reaching the controller. A
terminal that cannot be recorded leaves the operation non-successful: the
daemon never substitutes an in-memory claim of success for a durable record.

`OperationDomain.stop` admits a durable, attributable priority stop. It skips
the readiness check ordinary motion must pass — a recovering or uncertain
session is exactly when an operator needs it — and accepts `None` for the
observed generation, the API's `*`, to request the attempt despite a stale
view. The worker clears queued work and preempts the active request through the
protocol library's exact universal stop.

Its trusted terminal is the exact ID-less `stopped` line. Without it, the stop
operation *and* the work it affected are `UNCERTAIN`, never stopped-successful.
A stop that cannot be recorded is refused: an unattributable software stop is a
claim this daemon does not make, and the physical E-stop is the independent
safety device. This is a software stop, not an emergency stop.

## Device policy and the DTR gate

`cs71d.device.create_transport_factory` turns configuration into the factory
the worker thread calls to open its transport; the factory is never invoked at
policy time, so construction still happens on the owning thread.

Pre-open DTR suppression is only guaranteed by the Windows pyserial backend.
Linux and other POSIX behavior is recorded as `DTR_GATE_STATUS =
"NOT_EXECUTED"`, so opening a real port there raises `DtrGateError` instead of
opening one and hoping the controller does not reset. The Raspberry Pi
deployment path is consequently blocked until that gate is closed with
hardware evidence; simulator runs cannot close it.

## Deterministic simulator

`cs71d.simulator.SimulatorTransport` implements the same byte-stream boundary
used by `ProtocolClient`. It starts in v1, supports legacy/v2 discovery,
activation snapshots, queue inspection, optional CRC transitions, lifecycle
events, reset, and priority stop. Scheduled physical-operation terminals appear
only after tests call `advance(milliseconds)`; simulator code never sleeps.

Every instance logs and exposes an identity beginning with `SIMULATOR_ONLY`.
Its transcripts and CI evidence cannot satisfy hardware, DTR, motion, or HIL
gates.

Named adverse scenarios cover feed-overtravel fault, disconnect, malformed
frame, timeout, event gap, and terminal mismatch behavior. Strict fixture
replay loads the repository's normative v1 and v2 wire transcripts and rejects
host-byte drift instead of silently adapting it.

From the repository root:

```sh
python -m pip install --require-hashes -r appliance/daemon/requirements-dev.txt
python -m pip install --no-build-isolation -e ./host -e ./appliance/daemon
(cd appliance/daemon && ruff format --check . && ruff check . && mypy && pytest)
cs71d --check-config appliance/daemon/config/development.toml
```

With no config argument, `--check-config` validates a development profile using
the simulator backend and no device path. The production example accepts only
the stable `/dev/cs71` identity, and opening it stays blocked by the DTR gate.

`cs71d --serve [PATH]` runs the assembled daemon: the journal opens first
because nothing may be admitted that cannot be recorded, then the domain and
its serial worker, and only then the socket that lets anyone ask for work.
Shutdown unwinds in reverse on `SIGINT` or `SIGTERM`, so no new request is
admitted while the worker is still finishing the one it holds.

The service credential is read from the file named by `service_token_path`;
it is never a configuration value or a command-line argument, because both are
readable by any local user, and it is refused if the file is reachable by
others. Taking the socket path refuses to displace a daemon that is already
serving it — a second instance must not steal the path while the first still
owns the serial port.

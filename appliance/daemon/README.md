# cs71d

`cs71d` is the private machine-control daemon for the Raspberry Pi appliance.
This workspace currently provides the package boundary, strict configuration
validation, sole serial ownership, and published session state. The operation
domain, scheduling, persistence, and the Unix-socket API arrive in later
roadmap tasks.

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
the stable `/dev/cs71` identity; the scaffold never opens it.

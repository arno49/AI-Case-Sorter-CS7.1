# cs71d

`cs71d` is the private machine-control daemon for the Raspberry Pi appliance.
This workspace currently provides the package boundary, strict configuration
validation, and sole serial ownership. Session state, scheduling, persistence,
and the Unix-socket API arrive in later roadmap tasks.

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

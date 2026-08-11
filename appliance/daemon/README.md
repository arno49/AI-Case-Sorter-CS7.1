# cs71d

`cs71d` is the private machine-control daemon for the Raspberry Pi appliance.
This workspace currently provides the package boundary and strict configuration
validation. Serial ownership, scheduling, persistence, and the Unix-socket API
arrive in later roadmap tasks.

The package depends on the repository's `cs71_protocol` implementation and does
not duplicate framing or recovery logic.

## Deterministic simulator

`cs71d.simulator.SimulatorTransport` implements the same byte-stream boundary
used by `ProtocolClient`. It starts in v1, supports legacy/v2 discovery,
activation snapshots, queue inspection, optional CRC transitions, lifecycle
events, reset, and priority stop. Scheduled physical-operation terminals appear
only after tests call `advance(milliseconds)`; simulator code never sleeps.

Every instance logs and exposes an identity beginning with `SIMULATOR_ONLY`.
Its transcripts and CI evidence cannot satisfy hardware, DTR, motion, or HIL
gates.

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

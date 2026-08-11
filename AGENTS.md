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
API contract, initial daemon/web workspace scaffolds, and a single-owner serial
worker. Session state, the machine-control domain, and operator features are not
implemented; do not describe it as deployed or qualified.

## Current validated baseline

- Firmware version: `7.1.260714.6`.
- Reset and the default `uno` build remain legacy protocol v1.
- `uno_v2` compiles v2 support but still starts in v1 and requires explicit
  negotiation.
- `native`: 89 passing tests.
- `native_v2`: 49 passing tests.
- Host package: 116 passing pytest tests.
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
  `ProtocolClient` or a configured serial transport. Other daemon code submits
  typed intents to `SerialWorker` and never performs serial I/O itself.
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
  architecture decisions, or qualification status change.
- Keep changes narrowly scoped; do not rewrite unrelated user changes or
  silently tighten legacy behavior.

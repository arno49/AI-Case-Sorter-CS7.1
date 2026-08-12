# AI-Case-Sorter-CS7.1

This project builds a case sorter that uses machine vision and machine learning
to identify cartridge cases by headstamp. The repository contains the canonical
Arduino firmware, host protocol tooling, mechanical models, and the approved
architecture for a future Raspberry Pi 5 control appliance.

## Current software status

The canonical firmware is `7.1.260714.6`. Reset and the default `uno` build
remain protocol v1 for compatibility; `uno_v2` compiles opt-in v2 support while
also starting in v1.

| Environment | Current validated result |
| --- | --- |
| `uno` | 17,594 bytes flash / 899 bytes static SRAM |
| `uno_v2` | 26,290 bytes flash / 997 bytes static SRAM |
| `native` | 89 tests pass |
| `native_v2` | 49 tests pass |
| Python host package | 116 pytest tests pass |
| Python daemon/simulator | 298 pytest tests pass |

The Python package under `host/` implements the v1/v2 client, typed protocol
models, CRC, fail-closed recovery, and the `cs71-protocol` compatibility CLI.
Physical bench, Linux DTR, HIL, and Windows-application qualification remain
required; software tests do not satisfy those gates. See
[ArduinoCode/README.md](ArduinoCode/README.md) for firmware details and
[host/README.md](host/README.md) for the host API and CLI.

## Software checks

From the repository root, run the firmware environments and the no-hardware host
package checks:

```sh
pio run -e uno && pio run -e uno_v2
pio test -e native && pio test -e native_v2
(cd host && python -m pip install ".[dev]" "build==1.2.2.post1" && python -m pytest && python -m build --wheel --sdist)
python -m unittest discover -s appliance/contracts/tests -v
(python -m pip install --require-hashes -r appliance/daemon/requirements-dev.txt && python -m pip install --no-build-isolation -e ./host -e ./appliance/daemon && cd appliance/daemon && ruff format --check . && ruff check . && mypy && pytest && python -m build --no-isolation --wheel --sdist)
(cd appliance/web && npm ci && npm run check:api && npm run lint && npm run check && npm test && npm run build)
```

These checks do not replace firmware serial parity, HIL, or Windows
qualification.

## Raspberry Pi 5 appliance documentation

The Raspberry Pi 5 control appliance executive summary is
[RASPBERRY_PI_WEB_ARCHITECTURE.md](RASPBERRY_PI_WEB_ARCHITECTURE.md). Its canonical
architecture, ADRs, delivery backlog, roadmap, and traceability are under
[docs/architecture/](docs/architecture/README.md). Firmware protocol behavior remains
in [ArduinoCode/PROTOCOL_V2.md](ArduinoCode/PROTOCOL_V2.md); the Python host-library
boundary is documented in [host/README.md](host/README.md).

The appliance architecture and executable private
[`cs71d` OpenAPI v1 contract](appliance/contracts/cs71d-v1.openapi.json) are
approved, and initial Python daemon/SvelteKit SSR workspaces now exist under
`appliance/`. The daemon workspace includes a deterministic, explicit-clock
protocol simulator for software-only development, and a single-owner serial
worker that confines all controller I/O to one dedicated thread and publishes
its session state. Opening a real serial port on Linux stays blocked by the
unqualified DTR gate. The web workspace now authenticates local operators: its
own `web.db`, Argon2id password hashing, opaque server-side sessions behind a
deny-by-default request hook, and one-time expiry-bound bootstrap provisioning
instead of a default password. Roles are enforced server-side against the
documented capability matrix, with every route declaring what it requires, and
state-changing requests must pass an origin and CSRF check and stay inside
documented rate, concurrency and size budgets. A socket-only daemon client backs
a dashboard that reads the machine snapshot and submits the software stop,
recording every attempt in the web audit. The dashboard also watches the machine
live: one reader of the daemon's event stream is fanned out to every open
browser, and a browser that may have missed something re-reads a snapshot rather
than presenting a state assembled from a gap. The remaining operator screens have
not been implemented yet.

The canonical firmware uses cooperative proximity settling: after the feed
sensor has been inactive longer than its debounce timeout, brass must keep the
sensor continuously active for the configured settle time. A sensor drop
cancels and restarts the full interval; brass already active at boot does not
settle until a qualifying absence occurs.

Sorter pre-homing jogs and the 40 ms `sorttest:` pacing interval are also
cooperative. Serial commands, including `stop`, remain responsive between
sorter steps and diagnostic moves. Runtime code uses no millisecond `delay()`;
only the microsecond delays that form and pace individual step pulses remain.

The `test:` and `sorttest:` diagnostics use the independent
`SORTER_SLOT_COUNT` setting (default 8), selecting slots from 0 through 7 by
default. It can be changed for the current session with the exact serial command
`slotcount:{count}`; see [ArduinoCode/README.md](ArduinoCode/README.md) for
limits and application compatibility details.

## Instructions
Most of the information you need to build this project is available in [Instructions.pdf](Instructions.pdf).

---------
## Parts List
The parts list for this project can be found here:
[Parts List](https://www.reloadingrecipes.com/HeadstampSorter/Partslist)

--------

# CS7.1 Video Build Series

The release video for this project is here:

[![Watch the video](https://img.youtube.com/vi/s7dy0odA44U/hqdefault.jpg)](https://youtu.be/s7dy0odA44U)


Follow along with me as I build the various components of this system. 

## Classifier
[![Build the Classifier](https://img.youtube.com/vi/lhxDmvg5AVQ/hqdefault.jpg)](https://youtu.be/lhxDmvg5AVQ)

## Sorter
[![Build the Sorter](https://img.youtube.com/vi/rP7bBV_uqF4/hqdefault.jpg)](https://youtu.be/rP7bBV_uqF4)

## Camera Module
[![Build the Camera](https://img.youtube.com/vi/iOc7inAcXpQ/hqdefault.jpg)](https://youtu.be/iOc7inAcXpQ)

## Electronics
[![Build the Electronics](https://img.youtube.com/vi/cS54LOCpNGc/hqdefault.jpg)](https://youtu.be/cS54LOCpNGc)
 
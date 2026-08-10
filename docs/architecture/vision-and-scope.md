# Vision and Scope

## Product goal

Provide a Raspberry Pi 5 appliance that lets authenticated local-network operators observe and command one CS7.1 sorter through a SvelteKit server-rendered interface while preserving the firmware and physical safety boundaries. The appliance is a single-machine controller, not a cloud control plane.

## Actors and use cases

| Actor | Authorized use |
| --- | --- |
| Viewer | Read machine snapshot, operations, faults and system health. |
| Operator | Viewer use plus connect, home, validated sort/feed actions and software stop. |
| Administrator | Operator use plus local users, configuration, recovery/reset and maintenance. |
| Technician | Performs controlled physical installation, DTR experiments and hardware qualification; this is a procedure role, not necessarily a web role. |

MVP use cases are: sign in; see a trusted snapshot; connect and verify the controller; home; submit one validated operation; follow its progress; issue priority software stop; diagnose faults; and recover only with explicit operator intent. Completion shown to a user means `cs71d` received a trusted, correlated firmware terminal result, not that an HTTP request returned 2xx.

## MVP boundary

MVP includes SvelteKit SSR/Node.js BFF/UI, local users and sessions, `cs71d`, the existing `cs71_protocol` library, internal HTTP/JSON over a Unix domain socket, REST commands, SSE events, separate SQLite stores, systemd, udev, Caddy, Pi OS installation and a deterministic simulator.

MVP excludes remote/cloud tenancy, multi-controller coordination, direct browser serial/USB, arbitrary protocol-console commands, classifier implementation, mobile-native clients, Internet dependence, container deployment, and replacing firmware motion or emergency-stop circuitry.

## Constraints and existing facts

- The current host boundary is the Python `cs71_protocol` package: `ProtocolClient` owns v1/v2 discovery, strict framing, correlation, CRC transitions and fail-closed protocol recovery; `SerialTransport.open()` deliberately rejects POSIX/macOS as a guaranteed pre-open DTR path. See [host/README.md](../../host/README.md) and [transport.py](../../host/src/cs71_protocol/transport.py).
- Firmware protocol requirements are not redefined here; see [PROTOCOL_V2.md](../../ArduinoCode/PROTOCOL_V2.md).
- The daemon API is internal-only. Caddy exposes SvelteKit, never `cs71d`.
- Native Raspberry Pi OS is the MVP operating environment.

## Measurable NFR and SLO targets

These are targets to measure in qualification, **not validated results**.

| ID | Target |
| --- | --- |
| NFR-01 | `cs71d` reports an accepted priority-stop request within 250 ms under the defined Pi load test, excluding unbounded firmware/physical stop time. |
| NFR-02 | 99% of internal snapshot reads complete within 100 ms during the defined Raspberry Pi 5 load profile. |
| NFR-03 | A healthy Node/SvelteKit restart neither closes nor duplicates the daemon serial session or active operation. |
| NFR-04 | Every accepted state-changing request has an immutable `operation_id`, initiator correlation and durable terminal/journal outcome, or the daemon enters a surfaced non-ready fault state. |
| NFR-05 | The web UI meets WCAG 2.2 AA checks for MVP flows and works at 1280×720 CSS pixels. |
| NFR-06 | Bounded event buffers force resynchronization rather than silently dropping state changes. |
| NFR-07 | Supported Pi storage capacity and journal retention are measured before pilot; low disk space blocks new motion before durability is endangered. |

## Assumptions and gates

| Assumption | Required gate | If unmet |
| --- | --- | --- |
| Firmware provides trusted correlated v2 terminals for the selected commands. | Protocol parity and HIL terminal tests. | Do not display success or allow unattended operation. |
| A stable USB identity can be matched by udev. | Physical device enumeration test. | Do not enable automatic daemon connection. |
| POSIX open/reset behavior can be made safe for the chosen adapter. | DTR experiment in [deployment-and-operations.md](deployment-and-operations.md). | Linux DTR remains **NOT_EXECUTED**; appliance is development-only. |
| Firmware software `stop` behavior is suitable for the desired operating mode. | Stop-latency and fault HIL cases. | Require physical power isolation; do not represent it as E-stop. |
| SQLite can durably journal under expected storage faults. | Fault-injection and restore tests. | Reject new state-changing work and surface fault. |

The safety and evidence requirements are canonical in [security-and-safety.md](security-and-safety.md) and [testing-and-quality.md](testing-and-quality.md).

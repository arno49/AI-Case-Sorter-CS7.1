# Raspberry Pi Control Appliance Architecture

## 1. Purpose

This document defines a Raspberry Pi 5 appliance for operating a CS7.1 case
sorter through a server-rendered web application. It also decomposes delivery
into dependency-ordered tasks that can be developed without weakening the
firmware and hardware qualification gates.

The target is a maintainable single-machine deployment, not a distributed
cloud platform:

- SvelteKit provides the Node.js server-rendered user interface;
- a Python daemon, `cs71d`, owns the serial port and machine state;
- the existing `cs71_protocol` package remains the protocol implementation;
- the browser never accesses USB, serial, or `cs71d` directly;
- the appliance remains usable on a private LAN if Internet access is absent.

## 2. Decision summary

| Area | Decision | Reason |
| --- | --- | --- |
| Web framework | SvelteKit SSR on Node.js LTS | Lightweight SSR/PWA support and a small Raspberry Pi footprint |
| Hardware process | Python `cs71d` daemon | Reuses the tested Python protocol library and isolates hardware ownership |
| Browser updates | REST commands plus Server-Sent Events | Commands are request/response; machine updates are primarily server-to-client |
| Internal transport | HTTP over a Unix domain socket | Local-only, permission-controlled, observable, and OpenAPI-compatible |
| Persistence | Separate SQLite databases in WAL mode | No external database service; services do not share tables |
| Process supervision | `systemd` | Native restart, ordering, logging, limits, and device dependencies |
| Edge proxy | Caddy | LAN TLS, stable origin, and reverse proxying to SvelteKit |
| Packaging | Debian/Raspberry Pi OS services, not containers for MVP | Simpler USB, udev, Unix socket, and recovery behavior |
| API contract | OpenAPI plus generated TypeScript types | Prevents drift between Python and Node.js |

This is a modular monolith deployed as two processes. Splitting it into more
services would add operational failure modes without improving the machine
control boundary.

## 3. System context

```text
Operator browser / installed PWA
              |
              | HTTPS, secure session cookie
              v
       Caddy on the Raspberry Pi
              |
              v
       SvelteKit SSR application
       - authentication and RBAC
       - pages and server actions
       - validation for user-facing forms
       - audit attribution
       - SSE bridge
              |
              | HTTP over /run/cs71/cs71d.sock
              v
            cs71d
       - sole serial-port owner
       - protocol negotiation and recovery
       - command serialization
       - authoritative machine snapshot
       - operation/event journal
              |
              | USB serial, 9600 8N1
              v
       CS7.1 Arduino controller
              |
              v
     Motors, sensors, feeder, sorter
```

### Trust boundaries

1. The browser is untrusted and can only reach SvelteKit.
2. SvelteKit is trusted for identity and authorization, but not for motor
   timing or serial lifecycle.
3. `cs71d` is the only process permitted to open the controller device.
4. Firmware remains authoritative for low-level motion and faults.
5. A physical emergency stop and safe motor-power design remain outside the
   software trust boundary.

## 4. Component responsibilities

### 4.1 `cs71d`

`cs71d` is a long-running Python service built around `cs71_protocol`.

It owns:

- opening and locking the configured serial device;
- reset, v1 discovery, v2 activation, and fail-closed recovery;
- request-ID and terminal-response correlation;
- one serialized state-changing command lane;
- immediate priority handling for `stop`;
- the current connection, machine, queue, configuration, and fault snapshots;
- event sequence checking and `status` resynchronization;
- operation deadlines and cancellation;
- an append-only machine operation journal;
- a bounded event stream for local subscribers;
- health and readiness status.

It does not own:

- users, browser sessions, HTML, or public TLS;
- classifier image processing;
- arbitrary shell execution;
- the physical emergency-stop function.

The serial worker must not run in an async event-loop callback. A dedicated
worker thread or process consumes a bounded command queue, while the API layer
publishes immutable snapshots. Only the serial worker may call
`ProtocolClient`.

### 4.2 SvelteKit SSR application

The Node.js application owns:

- login, logout, session expiry, and role checks;
- SSR pages and PWA assets;
- operator intent validation;
- server-side calls to `cs71d`;
- user-attributed audit records;
- presentation of connection uncertainty and hardware gates;
- reconnection of browser event streams.

It must not infer successful motion from an HTTP 2xx response. A command is
complete only when `cs71d` returns a trusted protocol terminal result. If the
daemon reports `uncertain`, the UI must block dependent movement and require
recovery.

### 4.3 Caddy

Caddy terminates HTTPS and proxies only to SvelteKit. The `cs71d` Unix socket
is never exposed through Caddy. For an isolated LAN, certificates may use a
locally trusted CA; plain HTTP is acceptable only during development.

### 4.4 SQLite ownership

Use separate files:

- `/var/lib/cs71d/machine.db`: operations, protocol events, faults, recovery
  attempts, and configuration snapshots;
- `/var/lib/cs71-web/web.db`: users, sessions, preferences, and user-attributed
  audit metadata.

Neither service writes the other service's database. Cross-service identifiers
such as `operation_id` provide correlation. Machine control must continue to
fail safely if journaling fails; the failure is surfaced, not silently ignored.

## 5. Machine-control model

### 5.1 Connection state

```text
DISCONNECTED
  -> CONNECTING
  -> VERIFYING_V1
  -> ACTIVATING_V2
  -> READY

Any state -> RECOVERING -> READY
Any failed recovery -> UNCERTAIN
Device loss -> DISCONNECTED
```

`READY` means the protocol session and required snapshots were verified. It
does not mean the axes are homed or that the machine is physically safe.

### 5.2 Command classes

| Class | Examples | Scheduling |
| --- | --- | --- |
| Priority recovery | `stop` | Bypasses the normal command queue |
| State changing | home, sort, feed, diagnostics | Exactly one active operation |
| Read only | status, queue, capabilities, configuration | Allowed only where the protocol lifecycle permits |
| Administrative | reconnect, reset, enter/leave v2 | Exclusive session transition |

Every state-changing operation receives a daemon-generated UUID. Its state is:

```text
QUEUED -> ACCEPTED -> RUNNING -> SUCCEEDED
                            \-> FAILED
                            \-> CANCELLED
                            \-> UNCERTAIN
```

`SUCCEEDED` requires one trusted matching terminal response. Timeout,
disconnect, malformed framing, CRC uncertainty, or an unexpected terminal
causes recovery and never produces a success-shaped result.

### 5.3 Priority stop

`POST /api/v1/machine/stop` must:

1. bypass the state-changing queue;
2. ask `cs71d` to send the exact universal `stop`;
3. invalidate pending operation assumptions;
4. publish the resulting state immediately;
5. return whether a trusted stop acknowledgement was received.

This is a software stop, not a certified emergency stop. The UI must say so.

## 6. Internal API outline

The internal API is versioned independently from the Arduino protocol.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/health/live` | Process liveness only |
| `GET` | `/v1/health/ready` | Verified controller-session readiness |
| `GET` | `/v1/snapshot` | Complete immutable machine snapshot |
| `GET` | `/v1/events` | SSE event stream with monotonic daemon event IDs |
| `POST` | `/v1/session/connect` | Open and verify the configured controller |
| `POST` | `/v1/session/recover` | Stop/reset and establish a verified session |
| `POST` | `/v1/machine/stop` | Priority universal stop |
| `POST` | `/v1/operations/home` | Home feeder, sorter, or both |
| `POST` | `/v1/operations/sort` | Sort to a validated slot |
| `GET` | `/v1/operations/{id}` | Operation lifecycle and terminal result |
| `GET` | `/v1/operations` | Filtered operation history |
| `GET` | `/v1/configuration` | Current controller configuration |
| `PATCH` | `/v1/configuration` | Apply validated volatile configuration |

State-changing requests require:

- an authenticated operator at the SvelteKit boundary;
- an `Idempotency-Key`;
- the snapshot generation observed by the operator;
- a finite deadline;
- explicit confirmation for disruptive recovery or reset.

The daemon rejects stale generation numbers rather than executing an action
against a machine state the operator did not see.

### Event envelope

```json
{
  "event_id": 1842,
  "occurred_at": "2026-08-08T20:00:00Z",
  "generation": 73,
  "type": "operation.progress",
  "operation_id": "01988...",
  "data": {
    "phase": "sorter_homing"
  }
}
```

The SvelteKit SSE bridge resumes from `Last-Event-ID`. If the daemon's bounded
buffer no longer contains that event, it emits `snapshot.required`, and the
web application replaces local state from `/v1/snapshot`.

## 7. Web application

### 7.1 MVP screens

1. **Login** — local account authentication.
2. **Dashboard** — connection, mode, phase, homing, active fault, and active
   operation.
3. **Manual control** — home, select slot, sort, and priority stop.
4. **Queue** — reported drop and queued slots.
5. **Configuration** — capabilities-driven settings with generation display.
6. **Operations** — recent commands, progress, duration, user, and terminal
   result.
7. **Faults and recovery** — fault history and explicit recovery workflow.
8. **System** — firmware/protocol versions, daemon/web versions, storage, and
   health.

### 7.2 Roles

| Role | Permissions |
| --- | --- |
| Viewer | Status, queue, operation history, and faults |
| Operator | Viewer plus home, sort, feed, and stop |
| Administrator | Operator plus configuration, users, reset, and maintenance |

`stop` is available to every authenticated role. Anonymous access is disabled
by default.

### 7.3 UX rules

- Never optimistically show a machine command as complete.
- Keep the stop control visible and enabled during recovery or UI errors.
- Display `UNCERTAIN` more prominently than ordinary faults.
- Disable dependent commands when homing or capabilities are unknown.
- Use server-side validation even when the form has client-side constraints.
- Replacing an SSE connection must begin with a full snapshot.
- Do not expose raw arbitrary protocol commands in the operator UI.

## 8. Raspberry Pi deployment

### 8.1 Filesystem layout

```text
/opt/cs71/
  web/                         SvelteKit production bundle
  daemon/                      Python virtual environment and cs71d
/etc/cs71/
  cs71d.toml
  web.env
/var/lib/cs71d/
  machine.db
/var/lib/cs71-web/
  web.db
/run/cs71/
  cs71d.sock
/var/log/                      systemd journal is authoritative
```

### 8.2 Service ordering

```text
dev-cs71.device
      |
      v
cs71d.service
      |
      v
cs71-web.service
      |
      v
caddy.service
```

Use a udev rule to create a stable `/dev/cs71` symlink based on USB VID/PID and
serial number. `cs71d` runs as a dedicated user with only the required device
group. SvelteKit runs as a different user with permission to access only the
daemon socket.

### 8.3 Linux DTR qualification gate

The current CLI deliberately does not claim pre-open DTR suppression on
POSIX/macOS. Raspberry Pi deployment therefore requires a dedicated experiment
before unattended operation:

1. measure DTR and controller reset behavior for the selected USB adapter;
2. verify what happens when the daemon opens the port after a client crash;
3. verify startup with the machine idle and during representative motion;
4. decide whether the appliance may rely on deliberate reset-to-v1;
5. add a hardware motor-enable interlock if safe takeover cannot be guaranteed.

Until this is qualified, Raspberry Pi deployment is a development appliance,
not an unattended production controller.

## 9. Security and safety requirements

- Bind SvelteKit to loopback behind Caddy.
- Bind `cs71d` only to its Unix socket.
- Use secure, HTTP-only, same-site session cookies.
- Hash local passwords with Argon2id.
- Protect state-changing routes against CSRF.
- Enforce authorization in SvelteKit server code, not browser components.
- Apply request-size, command-rate, and login-rate limits.
- Never accept a device path, command string, or executable from an ordinary
  operator request.
- Redact secrets while retaining protocol and operation diagnostics.
- Back up configuration and databases without blocking the serial worker.
- Treat USB disconnect as unknown machine state.
- Provide a physical emergency stop and guarded motor-power circuit.

## 10. Observability

Minimum signals:

- service version and uptime;
- controller connection and protocol mode;
- last verified snapshot generation;
- active operation and phase;
- serial reconnect and recovery counts;
- protocol rejects, CRC failures, event gaps, and timeouts;
- queue depth and command latency;
- SQLite health and free disk space.

Use structured JSON logs with `operation_id`, `request_id`, and
`snapshot_generation`. Do not expose protocol request IDs as durable business
identifiers because they wrap and are session-scoped.

## 11. Testing strategy

| Layer | Without hardware | With hardware |
| --- | --- | --- |
| Domain | State transitions, authorization, idempotency, stale generations | Not required |
| Protocol | Existing scripted v1/v2 and CRC fixtures | Real serial parity |
| Daemon | Simulated controller, reconnects, timeouts, faults, event gaps | DTR, USB loss, stop latency |
| Internal API | Contract, error mapping, SSE resume and overflow | Controller-backed smoke tests |
| SvelteKit | SSR, actions, RBAC, CSRF, accessibility, browser flows | Operator workflow |
| Deployment | Clean Pi image installation and service restart tests | Device enumeration and reboot |
| System | Full simulated sorting workflows | HIL and soak testing |

The simulator may prove application behavior, but it must never satisfy a
hardware acceptance criterion.

## 12. Delivery decomposition

### Dependency graph

```text
PI-00
  |
  +--> PI-01 --> PI-02 --> PI-03 --> PI-04 --> PI-05
  |                |                          |
  |                +--------------------------+
  |                                           v
  +--> PI-06 ------------------------------> PI-07 --> PI-08
                                                   \-> PI-09

PI-05 + PI-08 + PI-09 --> PI-10 --> PI-11 [hardware] --> PI-12 [pilot]
```

### Work packages

| ID | Deliverable | Depends on | Hardware | Size |
| --- | --- | --- | --- | --- |
| PI-00 | Approve ADRs, MVP boundary, API conventions, and safety assumptions | — | No | S |
| PI-01 | Create `appliance/daemon` and `appliance/web` workspaces, quality gates, and CI | PI-00 | No | M |
| PI-02 | Implement deterministic CS7.1 simulator and scenario fixtures | PI-01 | No | M |
| PI-03 | Implement `cs71d` serial worker, ownership, session state, and recovery | PI-02 | No | L |
| PI-04 | Implement operation scheduler, priority stop, snapshots, and journal | PI-03 | No | L |
| PI-05 | Implement Unix-socket REST/OpenAPI/SSE service | PI-04 | No | L |
| PI-06 | Implement SvelteKit SSR foundation, sessions, RBAC, CSRF, and PWA shell | PI-01 | No | L |
| PI-07 | Generate TypeScript API client and implement the SvelteKit BFF/SSE bridge | PI-05, PI-06 | No | M |
| PI-08 | Implement dashboard, manual control, queue, configuration, faults, and history | PI-07 | No | XL |
| PI-09 | Add systemd, udev, Caddy, installer, backup, upgrade, and rollback | PI-05, PI-06 | Pi only | L |
| PI-10 | Run software integration, security review, accessibility checks, and simulated soak | PI-08, PI-09 | Pi optional | L |
| PI-11 | Qualify DTR, real serial parity, physical completion, stop, faults, and reboot recovery | PI-10, firmware gates | Yes | XL |
| PI-12 | Controlled operator pilot, runbook validation, rollback drill, and release | PI-11 | Yes | L |

### PI-00 — Architecture decisions

Tasks:

1. Record ADRs for SvelteKit, `cs71d`, Unix sockets, SSE, and separate SQLite
   ownership.
2. Define the MVP command set and explicitly exclude raw protocol access.
3. Define snapshot, operation, fault, and event schemas.
4. Define authentication policy and initial provisioning.
5. Convert hardware assumptions into qualification cases.

Acceptance:

- Every process has one owner and one trust boundary.
- Stop, uncertainty, and disconnect behavior are explicit.
- The OpenAPI versioning and compatibility policy is approved.
- No hardware result is inferred from simulator behavior.

### PI-01 — Repository foundation

Tasks:

1. Add Python daemon packaging and Node/SvelteKit workspace.
2. Pin supported Python and Node.js LTS versions.
3. Add formatting, lint, type-check, unit-test, package-build, and dependency
   audit jobs.
4. Generate build metadata from Git commits.
5. Add development commands that require no controller.

Acceptance:

- A clean checkout builds both services.
- CI caches do not become required build inputs.
- Production dependency licenses and vulnerabilities are reported.

### PI-02 — Controller simulator

Tasks:

1. Model startup, v1 discovery, v2 activation, IDs, events, and optional CRC.
2. Model configurable operation timing without sleeping in tests.
3. Add home, sort, stop, fault, disconnect, malformed-frame, and event-gap
   scenarios.
4. Replay normative and golden protocol transcripts.
5. Clearly mark simulator-only evidence in test reports.

Acceptance:

- Scenarios are deterministic and seedable.
- The simulator cannot be selected accidentally in production.
- Old-firmware and v2-capable fixtures both work.

### PI-03 — Hardware daemon foundation

Tasks:

1. Wrap `ProtocolClient` in one dedicated serial worker.
2. Add bounded queues and finite deadlines.
3. Implement connection, verification, activation, and recovery states.
4. Publish immutable snapshots.
5. Handle daemon restart and stale serial locks.

Acceptance:

- No second code path can open the serial device.
- Transport uncertainty always becomes `UNCERTAIN` or verified recovery.
- Queue saturation is visible and fails closed.

### PI-04 — Machine domain

Tasks:

1. Implement operation UUIDs and lifecycle persistence.
2. Serialize state-changing operations.
3. Implement priority stop and cancellation.
4. Enforce capability, homing, fault, and snapshot-generation preconditions.
5. Add configuration-generation conflict handling.
6. Persist bounded diagnostic context for failures.

Acceptance:

- Every accepted operation reaches one terminal daemon state.
- Duplicate idempotency keys cannot execute motion twice.
- Stale UI state cannot issue dependent movement.

### PI-05 — Internal API

Tasks:

1. Publish OpenAPI schemas and error codes.
2. Implement liveness, readiness, snapshot, operation, configuration, and stop
   endpoints.
3. Implement SSE replay, overflow, heartbeat, and snapshot replacement.
4. Add Unix-socket permissions and peer restrictions.
5. Add contract and load tests.

Acceptance:

- The API is unreachable from the LAN directly.
- Slow SSE clients cannot block the serial worker.
- Generated TypeScript types match the published OpenAPI document.

### PI-06 — SvelteKit platform

Tasks:

1. Create the SSR application and PWA manifest.
2. Add local user provisioning, Argon2id passwords, and secure sessions.
3. Add Viewer, Operator, and Administrator authorization.
4. Add CSRF protection, validation, and security headers.
5. Add a consistent inaccessible/uncertain/fault UI vocabulary.

Acceptance:

- Unauthorized server actions are rejected server-side.
- Session and CSRF tests cover all state-changing routes.
- The shell remains usable after temporary daemon loss.

### PI-07 — BFF integration

Tasks:

1. Generate the TypeScript client from OpenAPI.
2. Connect to `cs71d` through the Unix socket.
3. Proxy commands with user, idempotency, deadline, and generation context.
4. Bridge SSE with reconnect and full-snapshot replacement.
5. Map daemon errors without converting them to generic success pages.

Acceptance:

- Browser code contains no serial or daemon credentials.
- Recovery and uncertainty retain their typed meanings end to end.
- Disconnect/reconnect tests do not duplicate operations.

### PI-08 — Operator experience

Tasks:

1. Implement the MVP screens.
2. Add confirmation for reset, recovery, and configuration changes.
3. Keep priority stop available across layouts and connection states.
4. Add responsive tablet and desktop views.
5. Add keyboard navigation, contrast, and screen-reader status announcements.
6. Add exportable operation and fault history.

Acceptance:

- No command is represented as complete before its terminal result.
- Invalid or unavailable commands explain the blocking precondition.
- Critical workflows pass automated browser and accessibility tests.

### PI-09 — Appliance deployment

Tasks:

1. Create dedicated system users, directories, and permissions.
2. Add udev device identity and `systemd` units with restart limits.
3. Configure Caddy and first-run certificate guidance.
4. Add atomic install/upgrade and rollback scripts.
5. Add database backup, restore, retention, and disk-space checks.
6. Document power-loss and read-only recovery procedures.

Acceptance:

- A clean Raspberry Pi OS image can be provisioned reproducibly.
- Reboot starts services in the intended order.
- Rollback preserves compatible databases and configuration.
- Web service compromise does not grant arbitrary device or root access.

### PI-10 — Software qualification

Tasks:

1. Run all firmware, host, daemon, API, web, and browser suites.
2. Run simulated reconnect/fault/stop soak tests.
3. Perform security and dependency review.
4. Measure CPU, memory, event latency, and disk growth on Raspberry Pi 5.
5. Produce a blocked-criteria report for hardware-only cases.

Acceptance:

- Software suites pass from a clean checkout.
- Resource budgets and log-retention limits are approved.
- Hardware-only criteria remain explicitly `NOT_EXECUTED`.

### PI-11 — Hardware qualification

Tasks:

1. Complete the existing firmware V2-08H/V2-09/V2-10/V2-11 gates first.
2. Characterize Raspberry Pi USB/DTR and reset behavior.
3. Verify serial parity against the real controller.
4. Exercise home, sort, feed, stop, fault, disconnect, reconnect, and reboot.
5. Measure stop and UI-to-daemon command latency.
6. Run repeated-cycle and power-interruption tests.

Acceptance:

- Web terminal results correspond to observed physical completion.
- Takeover after process/USB failure has a documented safe result.
- Stop and fault behavior meet the approved hardware criteria.
- Evidence includes versions, timestamps, traces, and pass/fail results.

### PI-12 — Pilot and release

Tasks:

1. Train operators and execute the runbook.
2. Run a limited-volume supervised pilot.
3. Validate backup, restore, update, and rollback.
4. Triage incidents and close release-blocking findings.
5. Tag immutable daemon/web artifacts and retain checksums.

Acceptance:

- Operator sign-off and release evidence are retained.
- Rollback is demonstrated, not assumed.
- Known limitations are visible in the UI and release notes.

## 13. MVP boundary

The first deployable MVP includes:

- one locally connected sorter;
- local accounts and three roles;
- status, homing, manual sort, queue, configuration, stop, faults, and history;
- offline LAN operation;
- simulated development and real-hardware qualification paths;
- reproducible Raspberry Pi installation.

Deferred:

- camera capture and ML inference;
- automatic headstamp-to-slot policy;
- multiple machines;
- cloud accounts or remote Internet control;
- mobile push notifications;
- fleet telemetry;
- arbitrary firmware flashing from the web UI.

Camera and classifier integration should later submit typed classification
results to the machine-domain scheduler. It must not bypass the same operation,
generation, homing, fault, and stop rules used by a human operator.

## 14. Release gates

A release is not production-ready until all are true:

- firmware hardware gates are complete;
- Raspberry Pi DTR/reset behavior is characterized;
- physical stop and emergency-stop strategy is approved;
- real serial parity passes;
- security, accessibility, backup, and rollback checks pass;
- operator documentation and supervised pilot are complete.

Until then, the application may be released only as a development preview with
hardware-dependent criteria marked `NOT_EXECUTED`.

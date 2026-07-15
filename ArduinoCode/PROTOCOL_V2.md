# CS7.1 protocol v2 design

## 1. Status and design rule

| Item | Value |
| --- | --- |
| Status | Approved implementation baseline; stages 1-3 implemented |
| Transport | Existing 9600-baud USB serial connection |
| Default after reset | Legacy protocol v1 |
| Activation | Explicit and volatile |
| Reference v1 contract | [`PROTOCOL.md`](PROTOCOL.md) |
| Delivery plan | [`PROTOCOL_V2_PLAN.md`](PROTOCOL_V2_PLAN.md) |

Protocol v2 is an evolution of v1:

```text
v2 request = familiar v1 command + optional request ID
v2 response = request ID + explicit accepted/progress/done/error
```

An operator who knows v1 must be able to understand and type v2 commands in an
ordinary serial terminal. V2 therefore preserves:

- lowercase command names;
- colon-separated setters such as `feedspeed:90`;
- numeric feed commands such as `3`;
- `xf:`, `sortto:`, `homefeeder`, `homesorter`, `getconfig`, `ping`, and `stop`;
- `done`, `stopped`, and the `error:` prefix;
- the existing two-position sorting pipeline.

V2 adds only concepts that v1 lacks:

- optional request IDs;
- explicit `accepted`, `progress`, `data`, `done`, and `error` responses;
- physical completion of movement and homing requests;
- `status`, `capabilities`, `protocolversion`, and `queue`;
- stable numeric error and fault codes;
- optional CRC-16.

V2 does not require a new USB driver, VID/PID, or hardware revision. This
document uses **MUST**, **SHOULD**, and **MAY** as normative requirements.

### 1.1 Implementation ADR

The protocol grammar, negotiation strings, ID rules, lifecycle words, error
codes, queue terminology, stop semantics, and CRC parameters in this document
are frozen for implementation. Wire-visible changes require a new reviewed ADR;
they must not be introduced as incidental firmware refactoring.

The supported host-tool target is Python 3 with `pyserial`, packaged as the
cross-platform `cs71-protocol` command-line application. Repository maintainer
`@arno49` owns protocol approval and coordinates access to representative bench
hardware and the unmodified Windows application. Hardware and Windows
qualification remain explicit release gates and are not implied by approval of
this ADR.

The migration is visible directly in a terminal:

| Operation | V1 | V2 |
| --- | --- | --- |
| Set feed speed | `feedspeed:90` -> `ok` | `@10 feedspeed:90` -> `@10 done:feedspeed=90` |
| Direct sort | `sortto:3` -> `ok`, then `ping` barrier | `@11 sortto:3` -> `accepted`, then physical `done:slot=3` |
| Feed | `3` -> `done` | `@12 3` -> `accepted`, progress, then `done` |
| Inspect state | Not available | `status` -> readable `@0 data:` lines |
| Stop | `stop` -> `stopped` | Same recovery command, or correlated `@99 stop` |

## 2. Compatibility invariants

A firmware implementing v2 MUST preserve these rules:

1. Every reset starts in v1.
2. Protocol mode is volatile and MUST NOT be stored in EEPROM.
3. V1 emits no new startup, state, progress, or fault lines.
4. Existing v1 requests, responses, timing, spelling, ranges, queue behavior,
   and errors remain byte-for-byte compatible.
5. The v1 `getconfig` JSON object gains no v2-only properties.
6. Only explicit successful negotiation enters v2.
7. Returning to v1 clears request IDs, event sequence, and CRC state.
8. V2 reuses the existing hardware state machine rather than creating a second
   motor-control implementation.
9. Firmware must still fit the Arduino Uno flash and SRAM limits.

Compatibility matrix:

| Application | Firmware | Behavior |
| --- | --- | --- |
| Existing Windows app | Existing firmware | Unchanged v1 |
| Existing Windows app | V1+v2 firmware | Unchanged v1 because reset defaults to v1 |
| New app | Existing firmware | Detects no v2 and uses its v1 adapter |
| New app | V1+v2 firmware | Explicitly activates v2 |

## 3. Transport and framing

V2 retains the v1 serial settings:

- 9600 baud;
- 8 data bits, no parity, 1 stop bit;
- no flow control;
- ASCII lines terminated by LF;
- CRLF accepted on input.

The v1 payload limit remains exactly 40 bytes. A line of 41 bytes received in
v1 still produces:

```text
error:command too long
```

The maximum complete v2 line is 64 bytes excluding CR/LF. This includes the
optional request-ID prefix and CRC suffix. Long responses are split into
several readable `data:` lines.

V2 accepts printable ASCII only. Defined values are decimal integers, booleans,
fixed symbols, firmware versions, or simple `key=value` tokens. V2 deliberately
does not define percent encoding or arbitrary free-form strings.

## 4. Human-readable wire grammar

### 4.1 Requests

```text
[@<id> ]<command>[*<crc16>]
```

Both forms are valid:

```text
feedspeed:90
@10 feedspeed:90
```

Rules:

- `<id>` is decimal `1..65535`;
- `@0` is reserved for firmware responses to requests without an ID;
- one space follows an explicit ID;
- the command payload uses the familiar v1 spelling;
- leading or trailing whitespace is invalid;
- an ID cannot be reused until its terminal response arrives;
- machine clients SHOULD use IDs;
- humans using a terminal MAY omit IDs.

Only one ID-less request may be active. A second ID-less request receives a
`busy` error on `@0`. This prevents two operations from producing ambiguous
responses on the same reserved ID. To inspect `status`, `queue`, or `ping`
during a long ID-less operation, type that read-only request with an explicit
ID, for example `@1 status`.

A host MUST NOT send `@0`. Firmware reports it as an uncorrelated
`reject:1003:bad_id`.

### 4.2 Responses

```text
@<id> accepted[:key=value ...]
@<id> progress:<key=value ...>
@<id> data:<key=value ...>
@<id> done[:key=value ...]
@<id> error:<code>:<name> [key=value ...]
```

For an ID-less request, firmware uses `@0`.

Meanings:

| Word | Terminal | Meaning |
| --- | --- | --- |
| `accepted` | No | Physical or multi-step operation started |
| `progress:` | No | Request-owned progress or liveness |
| `data:` | No | A result field or snapshot field |
| `done` | Yes | Operation completed successfully |
| `error:` | Yes | Operation failed or request was rejected |

Every syntactically valid request with a trusted, available ID receives exactly
one terminal `done` or `error:`. A synchronous query or setter may respond
directly with `done`; physical work responds with `accepted` first.

Examples:

```text
@10 feedspeed:90
@10 done:feedspeed=90

@11 sortto:3
@11 accepted:operation=sort
@11 done:slot=3
```

### 4.3 Events

Events are readable lines not owned by one request:

```text
!<sequence> state:<key=value ...>
!<sequence> fault:<code>:<name> [key=value ...]
!<sequence> reject:<code>:<name> [key=value ...]
```

Sequence numbers run from 1 through 65535 and wrap to 1. The modular successor
of 65535 is 1. Any received value other than the expected modular successor is
a gap and tells the host to request a fresh `status`.

Examples:

```text
!7 state:mode=running phase=feed_move
!8 fault:3001:feed_overtravel latched=1
!9 reject:1002:bad_crc
```

`fault:` is reserved for machine faults. `reject:` reports a frame whose ID
cannot safely receive a correlated response. A reject does not alter machine
state or terminate an unrelated request.

## 5. Discovery and mode switching

### 5.1 Safe discovery in v1

After `Ready` and the v1 `ping` idle barrier, a new client sends:

```text
protocol:2?
```

| Firmware | Response | Meaning |
| --- | --- | --- |
| V2-capable | `protocol:2 available` | V2 can be activated |
| Existing v1 | `ok` | Unknown legacy command; remain in v1 |

The complete response must be compared. Legacy `ok` MUST NOT be interpreted as
v2 support.

New firmware handles `protocol:2?` and `protocol:2` before the legacy pending
queue. If the machine or queue is busy, it immediately returns `error:busy`.

### 5.2 Entering v2

After the exact availability response:

```text
host   -> protocol:2
device -> protocol:2 ready
```

The response uses v1 framing. Firmware enters v2 only after transmitting its
LF. Activation:

- clears partial and pending legacy command data;
- starts event sequence at 1;
- reserves no request ID;
- leaves CRC disabled;
- preserves machine position, queue, and runtime configuration.

An old firmware replies `ok`. A client enters v2 only after the exact
`protocol:2 ready`.

### 5.3 Returning to v1

The v2 request is:

```text
@1 protocol:1
```

When idle:

```text
@1 done:protocol=1
```

Firmware switches after transmitting the terminal LF. If physical work is
active, it returns `error:2001:busy` and remains in v2.

When CRC is enabled, both `protocol:1` and its terminal response carry CRC.
CRC and all other v2 session state are cleared only after the terminal LF.

Reset is the authoritative recovery when the current parser or CRC state is
unknown. Reset always returns to v1 and resets volatile configuration.

## 6. Request lifecycle and concurrency

Firmware supports:

- one active state-changing request;
- one immediate read-only request;
- priority `stop`;
- no implicit pending state-changing command in v2.

Read-only requests allowed during physical work:

- `ping`;
- `version`;
- `protocolversion`;
- `capabilities`;
- `status`;
- `getconfig`;
- `queue`.

Another state-changing request terminates immediately:

```text
@44 error:2001:busy active_id=42
```

If a frame reuses an active ID, firmware cannot use that same ID for the
rejection because it belongs to the original request. It emits:

```text
!10 reject:1003:bad_id id=42
```

The original request remains active.

## 7. Command set

### 7.1 V1 command preservation

The v2 command payload is normally the exact v1 command. Only the response
becomes correlated and explicit.

| Operation | V1 payload retained in v2 | V2 terminal meaning |
| --- | --- | --- |
| Pipelined feed | `{slot}` | `done` after the feed cycle |
| Forced feed | `xf:{slot}` | `done` after the forced cycle |
| Direct sorter move | `sortto:{slot}` | `done` after physical movement |
| Feed home | `homefeeder` | `done` after homing and offset |
| Sorter home | `homesorter` | `done` after pre-jog, homing, and offset |
| Correlated stop | `@id stop` | `done:mode=stopped` |
| Combined diagnostic | `test:{count}` | `done` after all cycles |
| Sort diagnostic | `sorttest:{count}` | `done` after physical return to slot 0 |
| Firmware version | `version` | Version in `data:`, then `done` |
| Configuration | `getconfig` | One property per `data:`, then `done` |
| Liveness | `ping` | `done:uptime_ms=<n>` |
| Runtime setters | Existing exact `name:value` | `done:name=value` |

V2 also accepts readable aliases:

| Alias | Equivalent familiar command |
| --- | --- |
| `feed:{slot}` | Numeric `{slot}` |
| `forcefeed:{slot}` | `xf:{slot}` |
| `homeall` | Serialized `homefeeder` followed by `homesorter` |

The aliases are additive. Existing spellings remain canonical for
compatibility and require less firmware duplication because they already exist.

### 7.2 Inspection commands

V2 adds four lowercase commands:

| Command | Purpose |
| --- | --- |
| `protocolversion` | Active protocol major version |
| `capabilities` | Features and current geometry limits |
| `status` | Current machine snapshot |
| `queue` | Two-position pipeline snapshot |

Example:

```text
host   -> status
device -> @0 data:mode=running
device -> @0 data:phase=idle
device -> @0 data:feed_homed=1
device -> @0 data:sort_homed=1
device -> @0 data:motor_enabled=0
device -> @0 data:active_id=none
device -> @0 data:fault_code=0
device -> @0 data:queue_previous=0
device -> @0 data:queue_next=3
device -> @0 done
```

This ID-less example assumes no other ID-less request is active.

### 7.3 Capabilities

`capabilities` returns one or more fields per readable `data:` line:

```text
@2 capabilities
@2 data:protocol=2 max_line=64 crc=optional
@2 data:queue_depth=2 slot_max=102 slot_count=8
@2 data:pwm=0 airdrop=1 feed_sensor=1
@2 data:feed_home=1 sort_home=1
@2 done
```

Required keys:

| Key | Meaning |
| --- | --- |
| `protocol` | Active protocol major |
| `max_line` | Maximum v2 line bytes excluding CR/LF |
| `crc` | `none` or `optional` |
| `queue_depth` | Mechanical pipeline depth |
| `slot_max` | Current representable maximum slot index |
| `slot_count` | Diagnostic destination count |
| `pwm` | Camera LED PWM availability |
| `airdrop` | AirDrop support |
| `feed_sensor` | Feed proximity sensor support |
| `feed_home` | Feeder homing support |
| `sort_home` | Sorter homing support |

Unknown future keys must be ignored by hosts.

### 7.4 Status

Required fields:

| Key | Values |
| --- | --- |
| `mode` | `running`, `recovering`, `stopped` |
| `phase` | `idle`, `feed_wait`, `feed_move`, `feed_home`, `sort_move`, `sort_home`, `settling`, `airdrop`, `diagnostic` |
| `feed_homed` | `0`, `1` |
| `sort_homed` | `0`, `1` |
| `motor_enabled` | `0`, `1` |
| `active_id` | Request ID, `0`, or `none` |
| `fault_code` | Numeric code or `0` |
| `queue_previous` | Current mechanical destination |
| `queue_next` | Destination retained for the next cycle |
| `config_generation` | Incremented after every successful setter |

### 7.5 Configuration

V2 preserves every existing setter exactly:

```text
@10 feedspeed:90
@10 done:feedspeed=90 generation=4
```

Invalid values use a stable code:

```text
@11 feedspeed:250
@11 error:1006:out_of_range key=feedspeed min=1 max=100
```

Preserved names and ranges:

| Setter | Range |
| --- | ---: |
| `feedspeed:` | `1..100` |
| `feedsteps:` | `1..1000` |
| `feedhomingoffset:` | `0..200` |
| `sortspeed:` | `1..100` |
| `sortsteps:` | `1..100` plus geometry constraints |
| `sorthomingoffset:` | `0..200` |
| `slotcount:` | Geometry-dependent |
| `notificationdelay:` | `0..32767` |
| `slotdropdelay:` | `0..32767` |
| `automotorstandbytimeout:` | `0..4294967` |
| `debounceTimeout:` | `0..32767` |
| `debounceTime:` | `0..32767` |
| `airdropenabled:` | `true`, `false`, `1`, `0` |
| `airdroppredelay:` | `0..32767` |
| `airdroppostdelay:` | `0..32767` |
| `airdropdsignalduration:` | `0..32767` |
| `cameraledlevel:` | Signed int32, effective `0..255` |

The mixed-case debounce names and misspelled
`airdropdsignalduration:` remain intentional compatibility spellings.

`getconfig` keeps its familiar name but emits fields as readable lines rather
than one long JSON object:

```text
@12 getconfig
@12 data:FeedMotorSpeed=90
@12 data:FeedCycleSteps=70
@12 data:SortMotorSpeed=90
@12 data:SortSteps=20
@12 data:SlotCount=8
@12 data:NotificationDelay=90
@12 done
```

V2 includes `SlotCount`; v1 continues omitting it. Additional properties use the
same names documented in the v1 `getconfig` schema.

Configuration remains volatile.

### 7.6 Homing and direct sorter movement

```text
@20 homeall
@20 accepted:operation=home
@20 progress:phase=feed_home
!11 state:mode=recovering phase=feed_home
@20 progress:phase=sort_home
!12 state:mode=recovering phase=sort_home
!13 state:mode=running phase=idle
@20 done:feed_homed=1 sort_homed=1
```

`homefeeder`, `homesorter`, and `homeall` report `done` only after physical
completion. `sortto:` reports `done` only after the sorter stops.

`sortto:` requires a known sorter position and otherwise returns
`error:2002:not_homed`. Homing commands are recovery operations and do not
require the selected axis to be homed.

`sortto:` preserves its v1 queue side effect: after completion, both queue
positions equal the requested slot.

### 7.7 Pipelined feed

Numeric, `feed:`, `xf:`, and `forcefeed:` retain the two-position pipeline.
The supplied slot is queued for a later physical drop; the current cycle uses
the previously queued destination.

```text
host   -> @42 feed:3
device -> @42 accepted:operation=feed
device -> @42 data:drop_slot=0 queued_slot=3
device -> @42 progress:phase=feed_wait elapsed_ms=1000
device -> @42 progress:phase=feed_move
device -> @42 progress:phase=settling
device -> @42 done:drop_slot=0 queued_slot=3
```

While brass remains absent, `progress:phase=feed_wait` repeats at most once per
second as liveness. It is not completion.

`forcefeed:` and `xf:` bypass only material readiness. They do not bypass
homing, range, overtravel, or stop protection.

The host must log `drop_slot`; it must not assume the requested slot was used in
the same cycle.

### 7.8 Queue inspection

```text
@7 queue
@7 data:queue_depth=2
@7 data:queue_previous=0
@7 data:queue_next=3
@7 done
```

There is no logical queue-reset command. Resetting software queue state without
physically establishing sorter position can misroute brass. Use `sortto:` to
move physically and synchronize both queue positions.

### 7.9 Diagnostics

V2 retains `test:` and `sorttest:`:

```text
@50 sorttest:3
@50 accepted:operation=sorttest count=3
@50 progress:index=0 slot=2
@50 progress:index=1 slot=6
@50 progress:index=2 slot=1
@50 progress:phase=return_home
@50 done:count=3 final_slot=0
```

Unlike v1, `sorttest:` emits terminal `done` only after the physical return to
slot 0 completes.

### 7.10 Stop

Normal correlated stop:

```text
host   -> @99 stop
device -> @42 error:2004:cancelled by=99
device -> !14 state:mode=stopped phase=idle
device -> @99 done:mode=stopped
```

`stop` bypasses busy handling. It disables the shared motor driver, forces
`FEED_DONE_SIGNAL` low, cancels work and diagnostics, and invalidates both axes.

The exact ID-less line `stop` is also an out-of-band recovery command in both
protocol modes. It is recognized before framing and CRC:

```text
host   -> stop
device -> !15 state:mode=stopped phase=idle
device -> stopped
```

The literal `stopped` response is the only deliberate unframed v1 line allowed
inside a v2 session. Firmware remains in v2 with the same CRC setting.

This is a software stop, not a certified emergency stop.

## 8. Error and fault codes

Numeric codes are stable. Existing codes must never be reused.

### 8.1 Protocol errors

| Code | Name | Meaning |
| ---: | --- | --- |
| 1001 | `bad_frame` | Invalid syntax, characters, or line length |
| 1002 | `bad_crc` | Missing or incorrect CRC |
| 1003 | `bad_id` | Invalid or already active request ID |
| 1004 | `unknown_command` | Command not implemented |
| 1005 | `invalid_argument` | Missing, extra, or malformed argument |
| 1006 | `out_of_range` | Value outside its current valid range |
| 1007 | `unsupported` | Feature unavailable in this build |
| 1008 | `unknown_key` | Requested property not implemented |

### 8.2 State errors

| Code | Name | Meaning |
| ---: | --- | --- |
| 2001 | `busy` | Another state-changing request is active |
| 2002 | `not_homed` | Required axis position is unknown |
| 2003 | `stopped` | Operation unavailable while stopped |
| 2004 | `cancelled` | Request cancelled by stop or reset |
| 2005 | `invalid_state` | Operation conflicts with current state |

### 8.3 Machine faults

| Code | Name | Status | Meaning |
| ---: | --- | --- | --- |
| 3001 | `feed_overtravel` | Defined | Feed home sensor not reached within threshold |
| 3002 | `feed_home_timeout` | Reserved | Future explicit feeder timeout |
| 3003 | `sort_home_timeout` | Reserved | Future explicit sorter timeout |
| 3004 | `sensor_fault` | Reserved | Future stuck/impossible sensor state |

A machine fault:

- emits one `fault:` event;
- terminates the active request with the same code and name;
- appears in `status`;
- invalidates affected positions;
- blocks dependent movement until recovery succeeds.

Invalid syntax, CRC, or ID that cannot be correlated emits `reject:` rather than
a fabricated response ID.

## 9. Optional CRC-16

CRC is off after activation. Enable it with the readable command:

```text
@1 crc:on
@1 done:crc=on*CF68
```

Disable it with:

```text
@2 crc:off*D690
@2 done:crc=off*48C9
```

The enable response is the first protected frame. The disable response remains
protected; CRC turns off after its LF.

| Parameter | Value |
| --- | --- |
| Algorithm | CRC-16/CCITT-FALSE |
| Polynomial | `0x1021` |
| Initial value | `0xFFFF` |
| Reflection | None |
| Final XOR | `0x0000` |
| Text encoding | Four uppercase hexadecimal digits |

CRC covers every byte before `*`, excluding CR/LF. When enabled, every framed
request, response, and event includes CRC.

Bad CRC does not execute the request:

```text
!16 reject:1002:bad_crc*452B
```

No request ID is trusted from that frame. The exact out-of-band `stop` is the
only CRC-exempt request.

CRC detects accidental corruption. It is not authentication or functional
safety.

## 10. Compatibility switcher

The reference CLI is `cs71-protocol`. It owns the serial port exclusively.

```text
cs71-protocol detect --port <port>
cs71-protocol enter-v2 --port <port> [--crc]
cs71-protocol leave-v2 --port <port>
cs71-protocol prepare-legacy --port <port>
cs71-protocol run-legacy --port <port> -- <application> [arguments...]
```

| Command | Behavior |
| --- | --- |
| `detect` | Safely stop/reset, wait for `Ready`, run the v1 barrier, and query support without activating v2 |
| `enter-v2` | Detect, activate, then read `protocolversion`, `capabilities`, and `status` |
| `leave-v2` | Send `protocol:1` in a known idle v2 session; otherwise stop and reset |
| `prepare-legacy` | Stop, reset, verify v1 startup, and release the port |
| `run-legacy` | Prepare v1, release the port, then launch the legacy application |

### 10.1 Detection

```text
open port at 9600 8N1 with automatic DTR reset suppressed
send exact "stop\n" and require "stopped"
toggle DTR reset unless --no-reset was requested
wait for exact "Ready"
send "ping\n" and require exact " ok"
send "version\n" and record the response
send "protocol:2?\n"

if response == "protocol:2 available":
    report v2 available
else if response == "ok":
    report v1 only
else if response == "error:busy":
    repeat the idle barrier and retry once
else:
    fail closed
```

The tool never sends `protocol:2` after legacy `ok`.

### 10.2 Activation

```text
perform detection
send "protocol:2\n"
require exact "protocol:2 ready"
switch local parser to v2
send "protocolversion", "capabilities", and "status"
validate required fields
optionally send "crc:on"
```

An unexpected response or timeout fails closed. The tool sends the universal
`stop`, resets, and verifies `Ready` before reporting recovery to v1.

### 10.3 Preparing the Windows application

1. Open the port exclusively without automatic DTR reset where possible.
2. Send universal `stop` and wait for `stopped`.
3. Deliberately reset the Uno.
4. Wait for `Ready`.
5. Complete the v1 `ping` idle barrier.
6. Do not activate v2.
7. Close and release the serial port.
8. Report that volatile configuration was reset.
9. Launch or permit the Windows application to open the port.

The Windows application may toggle DTR and reset the Uno again. The tool
guarantees v1 at handoff but cannot preserve volatile settings across that
second reset.

A platform unable to suppress automatic DTR while opening the port must report
that it could not guarantee delivery of `stop` before reset.

## 11. Recovery scenarios

| Scenario | Required action |
| --- | --- |
| New app with old firmware | Discovery receives `ok`; remain in v1 |
| Old app with new firmware | No activation occurs; firmware remains in v1 |
| Activation response lost | Stop/reset; never guess the active parser |
| CRC state uncertain | Send out-of-band `stop`, then reset to v1 |
| V2 client crashes | Next tool or app stops/resets and starts from v1 |
| Event sequence gap | Request `status` and replace local snapshot |
| Serial disconnect during motion | Treat machine state as unknown; stop and recover before resuming |

USB disconnect does not guarantee immediate motor stop. A future communication
watchdog and a physical emergency stop remain separate safety requirements.

## 12. Implementation constraints

Firmware should use:

- separate v1/v2 parsers sharing existing validated command handlers;
- one 65-byte physical input buffer;
- mode-specific 40-byte v1 and 64-byte v2 limits;
- the existing 41-byte pending buffer only in v1;
- no dynamic allocation or Arduino `String`;
- streamed `data:` responses;
- one compact active-request record;
- existing rollover-safe timers and machine state.

The v2 dispatcher should strip the optional ID and CRC, then pass the unchanged
v1 command payload to shared handlers wherever possible. This is the central
implementation mechanism that keeps v2 evolutionary and minimizes flash growth.

## 13. Required validation

### 13.1 Readability gate

Before implementation approval, a maintainer familiar only with v1 must be able
to:

- identify every retained command without a translation table;
- issue `status`, `sortto:`, a setter, feed, home, and stop from a plain terminal;
- explain `accepted` versus `done`;
- follow a feed trace and identify `drop_slot` versus `queued_slot`;
- recover to v1 without a protocol analyzer.

### 13.2 Native tests

- v1 remains byte-for-byte unchanged without activation;
- discovery distinguishes `protocol:2 available` from legacy `ok`;
- ID-less requests use `@0`;
- ID reuse and malformed frames emit `reject:`;
- each trusted request has one terminal response;
- familiar v1 payloads invoke shared handlers;
- aliases behave identically to canonical commands;
- physical commands emit `accepted` before terminal `done`;
- stop cancellation ordering is deterministic;
- status, capabilities, getconfig, and queue stream correctly;
- feed-wait liveness and queue values are correct;
- CRC known-answer and transition-boundary tests;
- reset from every v2 state restores v1.

### 13.3 Release gates

- Arduino Uno build passes with recorded flash/SRAM headroom;
- existing native tests remain passing;
- v2 tests pass in CI;
- existing Windows application completes configuration and a representative run
  against new firmware in default v1 mode;
- switcher handles old/new firmware, failed activation, CRC uncertainty, tool
  crash, and port contention;
- hardware qualification confirms homing, physical completion, queue reporting,
  camera-safe `done`, overtravel, and stop behavior.

V2 must not be released until legacy Windows compatibility and hardware
qualification are explicitly signed off.

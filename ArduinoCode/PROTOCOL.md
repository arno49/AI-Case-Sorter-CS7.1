# CS7.1 serial protocol specification

## 1. Scope and status

This document specifies the host-to-firmware interface required to implement an
alternative controller application for the CS7.1 sorter.

| Item | Value |
| --- | --- |
| Firmware | `7.1.260714.6` |
| Minimum documented AI Sorter version | `1.1.46` |
| Controller | Arduino Uno / ATmega328P |
| Canonical implementation | `CS71_Arduino/CS71_Arduino.ino` |
| Transport | USB CDC serial through the Uno USB interface |

An additive, backward-compatible successor is designed in
[`PROTOCOL_V2.md`](PROTOCOL_V2.md). It is currently a draft and is not
implemented by the firmware documented here.

The firmware source is authoritative if this document and an older PDF differ.
Physical validation of motors, sensors, AirDrop, PWM, and the Windows
application remains required for this firmware version.

This protocol covers mechanical control only. Camera discovery, image
acquisition, training data, model execution, confidence thresholds, class-to-
slot mapping, and profile persistence are application responsibilities and are
not implemented by the Arduino.

The terms **MUST**, **SHOULD**, and **MAY** describe requirements for a
compatible host implementation.

## 2. Transport and framing

Configure the serial port as:

- 9600 baud;
- 8 data bits, no parity, 1 stop bit;
- no software or hardware flow control.

Each request and response is an ASCII line terminated by LF (`0x0A`). The
firmware also accepts CRLF and removes one CR immediately before LF.

The request payload is limited to 40 bytes, excluding CR/LF. A host MUST NOT
send embedded NUL bytes.

| Framing condition | Firmware response |
| --- | --- |
| More than 40 payload bytes | `error:command too long` |
| Embedded NUL | `error:invalid command` |
| Empty or unknown nonnumeric command | Legacy response `ok` |

On overflow or embedded NUL, the complete frame is discarded through the next
LF. Parsing resumes with a clean buffer after that LF.

Opening the serial port commonly toggles DTR and resets an Uno. The host MUST
therefore treat reconnect as a firmware restart and reapply volatile settings.

## 3. Response model and command serialization

The protocol has no request IDs. Responses cannot be correlated when several
commands are in flight. A compatible host SHOULD use a strict one-command-at-a-
time model:

1. write one complete request line;
2. wait for that command's terminal response;
3. process unsolicited progress lines while waiting;
4. only then send the next request.

During active motion, homing, settling, or completion timing, the firmware can
retain one ordinary command. A second ordinary command received before the
pending command executes returns:

```text
error:busy
```

An exact `stop` frame bypasses this queue and is handled at the next cooperative
loop iteration. Do not use the one-command pending slot as normal application
flow control.

### 3.1 Terminal and intermediate lines

| Line | Meaning |
| --- | --- |
| `Ready` | Serial initialized; startup jog/homing may still be running. |
| `ok` | Synchronous command accepted. |
| ` ok` | Legacy `ping` response, including the leading space. |
| `done` | A feed cycle, homing, settling, and notification sequence completed. The next image may be captured. |
| `stopped` | Stop state entered and active work cancelled. |
| `waiting for brass` | Feed request remains pending because the proximity sensor is inactive. Repeats at most once per second. |
| `error:<detail>` | Current request or active operation failed. |
| `{...}` | One-line JSON response to `getconfig`. |

`ok` only acknowledges command acceptance. For `sortto:`, `homefeeder`, and
`homesorter`, it does not mean physical motion has completed.

### 3.2 Error catalogue

| Line | Cause and required host action |
| --- | --- |
| `error:command too long` | Frame exceeded 40 payload bytes. Correct the encoder and retry only after the line was discarded. |
| `error:invalid command` | Frame contained NUL. Treat as a transport/encoder defect. |
| `error:busy` | More than one ordinary command was sent while execution was busy. Stop pipelining and resynchronize. |
| `error:not homed` | A movement command was attempted without known positions. Run homing recovery. |
| `error:invalid slot` | Numeric destination was malformed or outside the representable range. |
| `error:invalid xf` | Forced-feed destination was malformed or outside the representable range. |
| `error:invalid sortto` | Direct-move destination was malformed or outside the representable range. |
| `error:invalid <setting>` | Setter or diagnostic value failed strict parsing/range validation. |
| `error:feed overtravel detected` | Feed homing sensor was not reached within the safety threshold. Stop the run and recover mechanically and logically. |

## 4. Machine states

The firmware has three host-visible operating modes even though no command
currently reports the mode directly:

| Mode | Description |
| --- | --- |
| `Recovering` | One or both axis positions are unknown or homing is active. |
| `Running` | Both positions are known; movement and feed commands are accepted. |
| `Stopped` | Motor driver disabled, active work cancelled, both positions unknown. |

The following commands require `Running`:

- numeric slot commands;
- `xf:`;
- `sortto:`;
- `test:`;
- `sorttest:`.

When position is unavailable they return:

```text
error:not homed
```

Configuration setters, `version`, `getconfig`, `ping`, `homefeeder`,
`homesorter`, and `stop` remain available while stopped or recovering.

## 5. Connection and initialization sequence

A host SHOULD perform this sequence after every serial connection:

1. Open the port and discard any incomplete pre-connection bytes.
2. Wait for `Ready`.
3. Send `ping`.
4. Wait for the exact ` ok` response. Because startup motion is busy, this
   command normally occupies the pending slot and acts as an idle barrier.
5. If any `error:` line is observed before the barrier response, treat startup
   as failed even if the queued `ping` later responds.
6. Send `version` and verify a supported firmware version.
7. Send `getconfig`.
8. Apply required runtime settings one at a time, waiting for `ok` after each.
9. Apply `slotcount:` separately because it is intentionally absent from
   `getconfig`.

All runtime configuration is volatile and resets to compile-time defaults after
power loss, reset, reconnect-induced DTR reset, or firmware upload.

## 6. Core command contract

### 6.1 Feed and sorter commands

| Request | Valid value | Immediate response | Terminal behavior |
| --- | --- | --- | --- |
| `{slot}` | `0..floor(32767 / (SortSteps * SORT_MICROSTEPS))` | None | Queues the destination, waits for brass if required, positions the sorter for the previously queued case, advances/homes the feeder, then emits `done`. |
| `xf:{slot}` | Same as numeric slot | None | Same cycle, but bypasses feed-sensor readiness; emits `done`. |
| `sortto:{slot}` | Same as numeric slot | `ok` | Direct diagnostic sorter move. No completion line is emitted. |
| `homefeeder` | No value | `ok` | Starts feeder recovery. No completion line is emitted. |
| `homesorter` | No value | `ok` | Runs the cooperative sorter pre-jog and then sorter recovery. No completion line is emitted. |
| `stop` | No value | `stopped` | Cancels motion, homing, diagnostics, pending command, and completion timing; disables the shared motor driver and forces `FEED_DONE_SIGNAL` low. |

For the canonical build, `SORT_MICROSTEPS` is 16. With default `SortSteps=20`,
the protocol arithmetic permits positions 0 through 102. This arithmetic limit
does not imply that the installed mechanics provide that many physical slots.

Use `ping` as an idle barrier after a command that acknowledges before motion
completes:

```text
host -> sortto:3\n
device -> ok\n
host -> ping\n
device ->  ok\n
```

The `ping` response is delayed until the sorter becomes idle.

### 6.2 Diagnostics

| Request | Valid value | Output | Invalid response |
| --- | --- | --- | --- |
| `test:{count}` | `0..32767` | `testing started`, then `N - S` progress and `done` for each feed/sort cycle. | `error:invalid test` |
| `sorttest:{count}` | `0..32767` | `testing started`, then `N - Sorting to: S`; finally `Sort Test Completed` after scheduling return to slot 0. | `error:invalid sorttest` |

Both diagnostics choose random destinations in `[0, slotCount)`.
`sorttest:` applies a cooperative 40 ms pacing interval before each move.

### 6.3 Inspection and liveness

| Request | Response |
| --- | --- |
| `version` | Firmware version only, for example `7.1.260714.6`. |
| `getconfig` | One-line JSON described in Section 8. |
| `ping` | Exact legacy line ` ok`, with one leading space. |

## 7. Runtime configuration setters

All setters use exact, case-sensitive command names. Values must consume the
entire string; whitespace, suffixes, fractions, overflow, and unsupported signs
are rejected. A valid setter emits `ok`; invalid input emits the listed error
and leaves the old value unchanged.

| Request | Accepted value | Unit/effect | Invalid response |
| --- | ---: | --- | --- |
| `feedspeed:{n}` | `1..100` | Feed motor speed scale | `error:invalid feedspeed` |
| `feedsteps:{n}` | `1..1000` | Full steps before feed homing search | `error:invalid feedsteps` |
| `feedhomingoffset:{n}` | `0..200` | Full steps after feeder sensor | `error:invalid feedhomingoffset` |
| `sortspeed:{n}` | `1..100` | Sort motor speed scale | `error:invalid sortspeed` |
| `sortsteps:{n}` | `1..100` | Full steps between destinations | `error:invalid sortsteps` |
| `sorthomingoffset:{n}` | `0..200` | Full steps after sorter sensor | `error:invalid sorthomingoffset` |
| `slotcount:{n}` | See below | Diagnostic destination count | `error:invalid slotcount` |
| `notificationdelay:{n}` | `0..32767` | Milliseconds before `done` | `error:invalid notificationdelay` |
| `slotdropdelay:{n}` | `0..32767` | Milliseconds before moving sorter when AirDrop is disabled | `error:invalid slotdropdelay` |
| `automotorstandbytimeout:{n}` | `0..4294967` | Inactivity seconds; 0 disables standby | `error:invalid automotorstandbytimeout` |
| `debounceTimeout:{n}` | `0..32767` | Inactive milliseconds that arm settling | `error:invalid debounceTimeout` |
| `debounceTime:{n}` | `0..32767` | Required continuous-active settling milliseconds | `error:invalid debounceTime` |
| `airdropenabled:{v}` | `true`, `false`, `1`, `0` | Enables AirDrop timing | `error:invalid airdropenabled` |
| `airdroppredelay:{n}` | `0..32767` | Milliseconds before AirDrop signal | `error:invalid airdroppredelay` |
| `airdroppostdelay:{n}` | `0..32767` | Sorter clearance delay used when AirDrop is enabled | `error:invalid airdroppostdelay` |
| `airdropdsignalduration:{n}` | `0..32767` | AirDrop high-pulse milliseconds | `error:invalid airdropdsignalduration` |
| `cameraledlevel:{n}` | Signed 32-bit integer | Clamped to PWM `0..255` | `error:invalid cameraledlevel` |

The mixed-case debounce names and misspelled
`airdropdsignalduration:` are protocol compatibility requirements.

Boolean text is case-insensitive. Numeric values otherwise use unsigned decimal,
except `cameraledlevel:`, which accepts a signed decimal before clamping.

### 7.1 Slot-count and sort-step ordering

`slotcount` controls diagnostics only; it does not restrict numeric, `xf:`, or
`sortto:` commands.

The accepted maximum is:

```text
floor(32767 / (SortSteps * SORT_MICROSTEPS)) + 1
```

`sortsteps:` is rejected if the existing `slotCount` would no longer fit.
When changing both values:

- reduce `sortsteps` before increasing `slotcount`;
- reduce `slotcount` before increasing `sortsteps`.

`SlotCount` is not included in `getconfig`. A replacement application MUST
track the value it sends or use the compile-time default of 8 after reset.

### 7.2 PWM feature detection

`CameraLEDLevel` appears in `getconfig` only when firmware was compiled with
`UseArduinoPWMDimmer=true`. A host SHOULD use property presence as the current
feature-detection mechanism and SHOULD NOT send `cameraledlevel:` when the
property is absent.

## 8. `getconfig` schema

The response is a single JSON object followed by LF. Values are numbers;
`AirDropEnabled` is serialized as `0` or `1`.

| Property | Meaning |
| --- | --- |
| `FeedMotorSpeed` | Effective feed speed scale |
| `FeedCycleSteps` | Feed full-step count |
| `SortMotorSpeed` | Effective sorter speed scale |
| `SortSteps` | Full steps between destinations |
| `NotificationDelay` | Milliseconds before `done` |
| `SlotDropDelay` | Non-AirDrop sorter clearance milliseconds |
| `AirDropEnabled` | `0` or `1` |
| `AirDropPostDelay` | AirDrop sorter clearance milliseconds |
| `AirDropPreDelay` | AirDrop pre-signal milliseconds |
| `AirDropSignalTime` | AirDrop high-pulse milliseconds |
| `FeedHomingOffset` | Feeder full-step offset |
| `SortHomingOffset` | Sorter full-step offset |
| `AutoMotorStandbyTimeout` | Standby seconds |
| `DebounceTimeout` | Absence timeout milliseconds |
| `DebouncePauseTime` | Continuous-active settling milliseconds |
| `CameraLEDLevel` | Optional PWM level; property omitted in non-PWM builds |

Default non-PWM response:

```json
{"FeedMotorSpeed":90,"FeedCycleSteps":70,"SortMotorSpeed":90,"SortSteps":20,"NotificationDelay":90,"SlotDropDelay":400,"AirDropEnabled":0,"AirDropPostDelay":0,"AirDropPreDelay":30,"AirDropSignalTime":50,"FeedHomingOffset":3,"SortHomingOffset":0,"AutoMotorStandbyTimeout":60,"DebounceTimeout":300,"DebouncePauseTime":500}
```

A host SHOULD ignore unknown JSON properties for forward compatibility.

## 9. Pipelined classification and feed cycle

The sorter uses a two-position queue because a case is classified at the camera
before it reaches the drop position. The destination sent with the current
command is stored for a later physical drop. During that command, the sorter
moves to the destination queued by the preceding command.

After homing, both queue positions are slot 0. Consequently:

```text
send 1 -> feeder advances; sorter remains at 0; destination 1 is queued
send 1 -> sorter moves to 1; feeder advances; next destination 1 is queued
```

A replacement application MUST NOT model a numeric command as “move directly
to this slot and then feed.” `sortto:` is the direct-movement diagnostic and
also rewrites the internal queue position; it should not be mixed into a normal
classification run.

### 9.1 Recommended run loop

1. Complete initialization and confirm the machine is idle.
2. Establish an application policy for the initial slot-0/catch-all pipeline
   state.
3. Capture the current camera image only after `done`.
4. Classify the image and map the class to a configured destination.
5. Send that destination as a decimal line.
6. Accept zero or more `waiting for brass` lines.
7. On `done`, return to step 3.
8. On any `error:`, stop the run loop and enter explicit recovery.

The application must also define how it primes the first case and flushes the
last queued classification. The protocol does not expose queue depth or provide
a dedicated flush operation.

## 10. Stop, faults, and recovery

`stop` is a software stop, not a certified emergency stop and not a replacement
for physical power isolation.

On `stop`, firmware:

- disables the shared motor driver;
- forces `FEED_DONE_SIGNAL` low;
- cancels feed, sort, homing, diagnostics, completion timing, and pending work;
- invalidates both axis positions;
- emits `stopped`.

To recover:

1. send `homefeeder` and wait for `ok`;
2. send `ping` and wait for ` ok` as the feeder-idle barrier;
3. send `homesorter` and wait for `ok`;
4. send `ping` and wait for ` ok` as the sorter-idle barrier;
5. abort recovery if any `error:` line appears;
6. re-read/apply configuration if the controller reset.

The two home commands may be issued in either order, but serializing each with
a `ping` barrier avoids consuming the single pending-command slot.

### 10.1 Feed overtravel

The terminal line is:

```text
error:feed overtravel detected
```

The motor driver is disabled and feeder position becomes unknown. If sorter
motion was active, sorter position also becomes unknown. Diagnostic cycles are
cancelled. Movement commands return `error:not homed` until every invalidated
axis is homed.

An already accepted pending command is not silently discarded by this fault.
It executes under recovery restrictions: movement receives `error:not homed`,
while inspection or homing commands remain available.

## 11. Client implementation guidance

Use separate components for:

- serial transport and reconnect detection;
- LF/CRLF line framing;
- command serialization;
- response classification;
- machine/recovery state;
- volatile configuration;
- camera capture;
- classification-to-slot mapping;
- pipeline priming/flushing;
- structured logging and trace capture.

Recommended response classifier order:

1. exact `Ready`, `ok`, ` ok`, `done`, and `stopped`;
2. prefix `error:`;
3. exact `waiting for brass`;
4. JSON object;
5. known diagnostic progress lines;
6. expected version line;
7. unknown line logged as a protocol warning.

Do not trim lines before matching `ping`, because trimming hides the legacy
leading-space distinction.

Cycle timeout policy must account for indefinite `waiting for brass`. Treat
those lines as liveness, not completion. Homing and movement timeouts should be
configurable because speed, offsets, and mechanics vary.

### 11.1 Reference command executor

The following pseudocode shows the required serialization. `readLine()` must
preserve leading spaces and remove only the line terminator.

```text
execute(request, terminalPredicate):
    assert no command is currently in flight
    serial.write(request + "\n")

    loop:
        line = readLine(configuredTimeout)
        log(direction="device", line=line)

        if line starts with "error:":
            raise ProtocolError(line)

        if line == "waiting for brass":
            notifyWaitingForMaterial()
            continue

        if isKnownProgress(line):
            publishProgress(line)
            continue

        if terminalPredicate(line):
            return line

        publishProtocolWarning(line)
```

Examples of terminal predicates:

| Operation | Terminal predicate |
| --- | --- |
| Setter | `line == "ok"` |
| `ping` | `line == " ok"` |
| `getconfig` | Valid JSON object |
| Numeric or `xf:` feed | `line == "done"` |
| `stop` | `line == "stopped"` |
| `sortto:` / home acceptance | `line == "ok"`; follow with a separate `ping` barrier |

### 11.2 Minimum replacement-application workflow

A useful replacement for the Windows application should provide:

1. serial-port selection, reconnect/reset handling, version compatibility, and
   a raw timestamped protocol trace;
2. editable machine profiles containing every runtime setting plus
   `slotCount`;
3. initialization that reapplies the selected profile after every controller
   reset;
4. manual home, direct-slot, forced-feed, stop, and diagnostic controls;
5. camera selection and a repeatable capture configuration;
6. class/model management and an explicit class-to-slot map;
7. the pipelined run loop from Section 9, with no image capture before `done`;
8. clear `waiting for brass`, stopped, recovering, and fault UI states;
9. a recovery wizard that performs serialized homing and verifies each idle
   barrier;
10. durable run records containing image/model identity, prediction,
    confidence, requested slot, firmware version, profile, responses, and
    faults.

The ML subsystem may be implemented independently of the transport layer. Its
only wire-level output is a validated slot number for the current
classification. Keep manual movement and protocol diagnostics available even
when no camera or model is loaded.

## 12. Compatibility test checklist

Before controlling real hardware, exercise the client against a serial mock and
verify:

- LF and CRLF framing, partial reads, multiple lines per read, and a preserved
  leading space in `ping`;
- 40-byte acceptance, 41-byte rejection, and discard-through-LF recovery;
- NUL rejection;
- interleaved progress lines before a terminal response;
- indefinite `waiting for brass` without false completion;
- `error:busy`, `error:not homed`, and all invalid-value responses;
- JSON parsing with absent `CameraLEDLevel` and unknown future properties;
- startup `Ready` followed by delayed barrier completion;
- stop preemption while another command is active;
- reset/reconnect causing configuration reapplication;
- pipeline priming, steady-state classification, and final flush policy.

Then perform a hardware qualification with material removed where possible:

- verify feeder/sorter direction, home-switch polarity, offsets, and slot
  alignment;
- verify stop latency and motor-enable behavior during every movement phase;
- induce and recover from feed overtravel;
- verify proximity settling and empty-input waiting behavior;
- measure camera-safe timing after `done`;
- verify AirDrop pulse polarity and timing before enabling it in production;
- verify every supported firmware/profile combination and retain the trace.

## 13. Known protocol limitations

An alternative application must currently work around these limitations:

- no request IDs or response correlation;
- no `status` command exposing `Running`/`Stopped`/`Recovering`;
- no explicit homing-complete event;
- no capabilities or protocol-version command separate from firmware version;
- no queue-depth or flush command;
- `slotCount` absent from `getconfig`;
- PWM capability inferred from optional JSON property presence;
- unknown commands return success-shaped `ok`;
- legacy leading space in `ping`;
- legacy mixed-case and misspelled setter names;
- volatile settings with no EEPROM persistence.

These should be addressed through additive, versioned protocol extensions.
Changing legacy behavior in place would risk incompatibility with the existing
Windows application.

The proposed extension and compatibility switcher are specified in
[`PROTOCOL_V2.md`](PROTOCOL_V2.md).

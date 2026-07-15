# AI-Case-Sorter-CS7.1
This repo was created to isolate all the code and resources for the CS7.1 Version

## Release validation status

Firmware `7.1.260714.6` compiles for Arduino Uno with PlatformIO and has 65
passing native tests. CI verifies both on every firmware pull request. Physical
bench validation is still required before treating this firmware as a hardware
release: motor direction, homing and offsets, stop latency, AirDrop pulse
timing, PWM output, sensor behavior, and Windows application compatibility have
not been exercised from the development environment.

## Build and test

The canonical sketch remains
`ArduinoCode/CS71_Arduino/CS71_Arduino.ino`. Open it in Arduino IDE, select
**Arduino Uno**, and use **Verify** or **Upload**.

PlatformIO provides an optional command-line workflow from the repository root:

```sh
pio run -e uno
pio test -e native
```

Run one native test suite with:

```sh
pio test -e native -f test_logic
pio test -e native -f test_command_parser
pio test -e native -f test_machine_state
pio test -e native -f test_runtime_timer
pio test -e native -f test_feed_completion
pio test -e native -f test_proximity_settler
pio test -e native -f test_step_sequence
```

## Serial command framing

The firmware accepts commands at 9600 baud. Each command is terminated by
newline (`\n`); a single carriage return immediately before newline is ignored,
so CRLF is also accepted. Command payloads may contain up to 40 bytes.

If a payload exceeds 40 bytes, the complete line is discarded through its
newline and the firmware replies `error:command too long`. The next line starts
a fresh command. Runtime setting values must be complete integers within their
supported ranges. Boolean settings accept `true`/`false` (case-insensitive) or
`1`/`0`; malformed values return `error:invalid <setting>` without changing the
previous value.

`slotcount:{count}` sets the number of destinations used by both diagnostic
commands and replies `ok`. The value must be at least 1 and no greater than
`floor(32767 / (SortSteps * SORT_MICROSTEPS)) + 1`; invalid input replies
`error:invalid slotcount` and preserves the previous value. Changing
`sortsteps:` is also rejected when it would make the current diagnostic slot
count unsafe. Slot count does not limit ordinary numeric, `xf:`, or `sortto:`
commands, whose existing AVR-safe range is unchanged.

`SlotCount` is deliberately absent from `getconfig`: supported Windows
application versions have not been verified to tolerate an additional JSON
property. Set it manually in a 9600-baud serial terminal with, for example,
`slotcount:8`, or send that exact command from the application's advanced
configuration facility after its usual `sortsteps:` initialization. Runtime
settings reset at restart. To change the firmware default, edit
`SORTER_SLOT_COUNT` in the user configuration section.

Frames containing an embedded NUL byte are discarded with
`error:invalid command`.

## Serial command reference

| Command | Response and behavior |
| --- | --- |
| `{slot}` | Queues a destination and starts the pipelined feed/sort cycle; eventually emits `done`. |
| `xf:{slot}` | Same cycle while bypassing the feed sensor; eventually emits `done`. |
| `sortto:{slot}` | Acknowledges with `ok` and moves directly for diagnostics. |
| `homefeeder`, `homesorter` | Acknowledge with `ok` when recovery starts. |
| `stop` | Cancels active work, disables the shared driver, and emits `stopped`. |
| `test:{count}`, `sorttest:{count}` | Emit `testing started`, progress lines, and run the requested diagnostics. |
| `version` | Emits the firmware version alone. |
| `getconfig` | Emits one line of JSON containing the supported runtime settings. |
| `ping` | Emits the legacy response ` ok` with a leading space. |

Runtime setters acknowledge valid values with `ok` and use these exact names:
`feedspeed:`, `feedsteps:`, `feedhomingoffset:`, `sortspeed:`, `sortsteps:`,
`sorthomingoffset:`, `slotcount:`, `notificationdelay:`, `slotdropdelay:`,
`automotorstandbytimeout:`, `debounceTimeout:`, `debounceTime:`,
`airdropenabled:`, `airdroppredelay:`, `airdroppostdelay:`,
`airdropdsignalduration:`, and `cameraledlevel:`. Preserve the mixed-case
debounce names and the legacy extra `d` in `airdropdsignalduration:`.

Unknown nonnumeric commands retain the legacy `ok` response. Invalid setters
emit `error:invalid <setting>`; framing errors, busy state, unavailable position,
and feed overtravel use their documented `error:` responses.

## Stop and recovery

`stop` is a software control command, not a certified emergency stop or a
substitute for physically isolating motor power. A complete exact `stop` frame
is handled while feed, sort, or homing motion is active and replies exactly:

```text
stopped
```

It disables the shared motor driver, forces the feed-done output low, cancels
motion, homing, tests, completion notifications, and the pending command, and
marks both axis positions unknown. Serial input is checked on each cooperative
loop pass, including between pre-homing sorter jog steps and sorter diagnostic
moves.

The machine starts in `Recovering` mode and enters `Running` only after both
axes finish homing, including configured offsets. After `stop`, issue
`homefeeder` and `homesorter` (in either order); each replies `ok` when homing
starts. The motor driver is re-enabled for recovery. Once both axes are known,
the sorter queue is reset to zero and normal commands are accepted. Builds with
either homing sensor disabled mark that axis known deterministically when its
home operation is serviced.

The startup sorter pre-jog runs as one step per loop pass. Feeder and sorter
homing begin together only after that jog completes, preserving startup
ordering without blocking serial input. `homesorter` acknowledges immediately,
runs the same cooperative pre-jog, and then homes only the sorter. Stop and
feed faults cancel a pending jog so it cannot later start homing.

While stopped or recovering, `stop`, `version`, `getconfig`, `ping`, all runtime
configuration setters, `homefeeder`, and `homesorter` remain available. Numeric
feed/sort commands, `xf:`, `sortto:`, `test:`, and `sorttest:` reply:

```text
error:not homed
```

While execution is busy, one complete ordinary command can be held pending.
Any additional completed ordinary frame replies `error:busy`; it starts a new
frame and is never appended to the pending command. A pending command is not
dispatched until motion and homing are idle and any preceding feed-cycle
`done` notification has been emitted.

## Nonblocking feed completion

After feed homing completes, feed notification runs cooperatively without
`delay()`. With AirDrop enabled, firmware waits `AirDropPreDelay`, drives
`FEED_DONE_SIGNAL` high for `AirDropSignalTime`, drives it low, waits
`NotificationDelay`, and then emits `done`. Without AirDrop, it waits only
`NotificationDelay`. AirDrop mode and all three durations are snapshotted when
completion starts, so a queued runtime setter affects the next cycle.

Completion remains busy until `done` is emitted, preserving one-command pending
behavior. `stop`, feed overtravel, or leaving `Running` mode cancels the
completion, forces `FEED_DONE_SIGNAL` low, and suppresses stale `done` output.
`AirDropPostDelay` remains the independent slot/drop clearance gate.

## Cooperative sorter diagnostics

`sorttest:` uses a cooperative 40 ms `RuntimeTimer` gate before each random
sorter move. Both `test:` and `sorttest:` choose from the upper-exclusive range
`[0, slotCount)`. The independent default of 8 covers slots 0–7; this expands
the former `test:` default range of 0–5. Chute spacing and slot count must both
match the installed mechanics and are not derived from each other. The
return-to-zero move and pending-command blocking are unchanged. `stop` cancels
the pacing gate and test. Runtime firmware contains no `delay()` calls; only
`delayMicroseconds()` calls for step pulse width and motor speed remain.

## Proximity settling

After the feed sensor has remained inactive for longer than
`DebounceTimeout`, the next detected case must keep the sensor continuously
active for `DebouncePauseTime` before feeding can begin. This settling interval
is cooperative, so serial handling and `stop` remain responsive. If the sensor
drops inactive during settling, the timer is canceled and a full active
interval is required when the case returns. A case already active at boot does
not settle until a qualifying absence occurs.

`xf:` and builds with `FEEDSENSOR_ENABLED=false` bypass readiness without
clearing a pending settle requirement. Stop and feed-fault/recovery paths reset
active settling so stale expiry cannot start movement.

A feed-overtravel fault disables the motor driver and marks the feeder position
unknown. An already accepted pending command is processed afterward under the
normal recovery restrictions, so movement receives `error:not homed` while
diagnostics and homing remain available. Home the feeder successfully before
movement commands are accepted again. If sorter motion was active when the
fault occurred, it is cancelled and the sorter must also be homed.

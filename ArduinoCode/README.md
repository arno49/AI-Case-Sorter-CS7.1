# AI-Case-Sorter-CS7.1
This repo was created to isolate all the code and resources for the CS7.1 Version

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

Frames containing an embedded NUL byte are discarded with
`error:invalid command`.

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
loop pass. Existing blocking `delay()` calls and the synchronous sorter jog
still defer all serial handling, including `stop`, until they return.

The machine starts in `Recovering` mode and enters `Running` only after both
axes finish homing, including configured offsets. After `stop`, issue
`homefeeder` and `homesorter` (in either order); each replies `ok` when homing
starts. The motor driver is re-enabled for recovery. Once both axes are known,
the sorter queue is reset to zero and normal commands are accepted. Builds with
either homing sensor disabled mark that axis known deterministically when its
home operation is serviced.

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

A feed-overtravel fault disables the motor driver and marks the feeder position
unknown. An already accepted pending command is processed afterward under the
normal recovery restrictions, so movement receives `error:not homed` while
diagnostics and homing remain available. Home the feeder successfully before
movement commands are accepted again. If sorter motion was active when the
fault occurred, it is cancelled and the sorter must also be homed.

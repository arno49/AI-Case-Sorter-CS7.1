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

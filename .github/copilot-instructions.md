# Copilot instructions

## Build and hardware checks

- The maintained firmware sketch is `ArduinoCode/CS71_Arduino/CS71_Arduino.ino`. Open that sketch in Arduino IDE 2.x, select **Arduino Uno**, then use **Verify** to compile and **Upload** to flash it. The sketch directory must keep the same `CS71_Arduino` name as the `.ino` file.
- There is no repository-local build script, automated test suite, or linter configuration. `Wire` and `SoftwareSerial` are Arduino libraries; no additional dependency manifest is checked in.
- Hardware diagnostics use the Arduino IDE Serial Monitor at **9600 baud** with newline-terminated commands. After reset, expect `Ready`.
- Run one complete feed/sort diagnostic with `test:1`, or one sorter-only movement diagnostic with `sorttest:1`. Use `sortto:3` for a deterministic sorter movement, `homefeeder`/`homesorter` for homing, `getconfig` for current runtime settings, and `version` for firmware compatibility. A completed feed cycle emits `done`; configuration and homing commands generally emit `ok`.
- For bench testing without connected sensors, the build guide permits temporarily disabling `FEEDSENSOR_ENABLED`, `FEED_HOMING_ENABLED`, and `SORT_HOMING_ENABLED`. Treat this as a hardware-test configuration, not a default firmware change.

## Architecture

- This repository contains the physical CS7.1 design and its controller firmware, not the Windows AI application. `3DModels/` contains the canonical printable classifier, sorter, enclosure, and caliber-specific parts. `ArduinoCode/` contains the canonical Arduino Uno/CNC-shield firmware. `Mods/` contains approved optional designs; `CommunityContributions/` contains independent variants and should not be assumed to match the canonical hardware or firmware.
- The complete system has a classifier (feed wheel, camera, and lighting), a rotary sorter that directs cases into bins, and an Arduino-based electronics enclosure. The external Windows application captures and classifies headstamp images, then controls the Arduino over USB serial.
- The firmware is a cooperative state machine. `loop()` services serial input, proximity sensing, sorter movement/completion, feed scheduling/errors/movement/homing, notifications, diagnostics, and motor standby. Feed movement is blocked while sorting is active; both axes home through sensor-driven states and optional offsets.
- Sorting is pipelined by `qPos1`/`qPos2`: a numeric serial command supplies the newly classified destination, while the sorter moves to the previously queued destination before the next case is fed. Do not simplify this to an immediate “move to this slot, then feed” operation; the documented two-position queue intentionally delays sorting by one command.
- User-facing motor distances are full steps and are converted internally using `FEED_MICROSTEPS` or `SORT_MICROSTEPS`. The configured motors have 200 full steps per revolution and the CNC shield is normally set to 16 microsteps.

## Firmware conventions and compatibility

- Hardware defaults and compile-time options belong above `///END OF USER CONFIGURATIONS ///`; runtime state and behavior belong below it. The desktop application can temporarily override many defaults with `key:value` serial commands, but those values reset when the Arduino restarts unless the application reinitializes them.
- Preserve the serial protocol when changing firmware: commands are newline-delimited, numeric commands drive the queued feed/sort cycle, setters acknowledge with `ok`, `getconfig` returns one-line JSON, feed completion emits `done`, and errors use the `error:` prefix. The desktop application depends on exact command names and response timing.
- Serial commands are accepted only while neither a feed cycle nor sorter movement is in progress. `recvWithEndMarker()` accumulates input until `\n`; keep commands and responses on one line and reset the input buffer on every completed handler.
- Preserve these command families and response semantics:

  | Command | Behavior and response |
  | --- | --- |
  | `{slot}` | Queues `{slot}`, waits for brass when required, sorts the previously queued case, feeds, homes, then emits `done`. While waiting, emits `waiting for brass` at most once per second. |
  | `xf:{slot}` | Same cycle, but bypasses the feed proximity sensor; completion is asynchronous via `done`. |
  | `sortto:{slot}` | Moves directly to a slot for diagnostics and immediately acknowledges with `ok`; sorter movement continues through the state machine. |
  | `homefeeder`, `homesorter` | Starts homing and immediately acknowledges with `ok`. |
  | `stop` | Cancels feed/homing state without an immediate `ok`; the normal completion path may subsequently emit `done`. |
  | `test:{count}`, `sorttest:{count}` | Starts repeated hardware diagnostics and emits `testing started`, followed by progress lines. |
  | `getconfig` | Emits a single JSON object containing the effective runtime values. Keep existing property names stable when extending it. |
  | `version` | Emits `FIRMWARE_VERSION` alone. |
  | `ping` | Emits the legacy response ` ok` with a leading space. |

- Runtime setters use the exact lowercase spellings implemented in `checkSerial()`: `feedspeed:`, `feedsteps:`, `feedhomingoffset:`, `sortspeed:`, `sortsteps:`, `sorthomingoffset:`, `notificationdelay:`, `slotdropdelay:`, `automotorstandbytimeout:`, `debounceTimeout:`, `debounceTime:`, `airdropenabled:`, `airdroppredelay:`, `airdroppostdelay:`, `airdropdsignalduration:`, and `cameraledlevel:`. Preserve the existing mixed-case debounce names and the legacy extra `d` in `airdropdsignalduration:` unless the desktop application is updated in lockstep. Successful setters emit `ok`.
- Unknown nonnumeric commands currently receive `ok`; do not silently tighten that legacy behavior without confirming application compatibility.
- Feed overtravel aborts the cycle with `error:feed overtravel detected` and does not emit a success-shaped response. Keep error paths distinct from `done`/`ok`.
- Keep `FIRMWARE_VERSION` and the version banner at the top of the sketch synchronized. The current firmware declares the minimum compatible AI Sorter application version there.
- Store fixed serial messages in flash with Arduino's `F("...")` macro to conserve Uno SRAM. Dynamic values and the firmware version are printed separately.
- Pin assignments are coupled to hardware options. In particular, enabling `UseArduinoPWMDimmer` swaps the feed sensor from pin 9 to pin 13 and uses pin 9 for camera LED PWM; firmware changes to this option require the matching wiring change.
- Homing offsets and travel thresholds are represented in full steps in configuration, then converted to microsteps for state-machine counters. Recompute dependent counters when adding a runtime setter, following the existing `feedhomingoffset:`, `sorthomingoffset:`, and `feedsteps:` handlers.
- Timing settings have physical meaning: notification delay allows vibration to settle before image capture, slot-drop delay lets a case clear the sorter tube, and AirDrop pre/signal/post delays coordinate the optional solenoid. Preserve their ordering when changing completion behavior.
- Add canonical fixes to `ArduinoCode/CS71_Arduino/CS71_Arduino.ino`. Only edit a sketch under `CommunityContributions/` or `Mods/` when the task explicitly targets that variant; do not silently propagate variant-specific pins or board assumptions into the Uno firmware.
- For mechanical changes, keep canonical parts in the appropriate `3DModels/` subsystem and caliber-dependent feed wheels/nozzles under `3DModels/Classifier/Caliber Specific Parts/`. Contributor-specific alternatives remain grouped by contributor under `CommunityContributions/`.

The authoritative assembly and firmware workflow is in `Instructions.pdf`; application-side configuration, protocol behavior, camera resolutions, and model workflow are documented in `AI Headstamp Sorter Application Guide.pdf`.

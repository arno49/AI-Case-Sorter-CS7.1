# CS7.1 Firmware Improvement Plan

This roadmap covers `ArduinoCode/CS71_Arduino/CS71_Arduino.ino` version
`7.1.260131.1`. The goal is to improve correctness, stop responsiveness,
maintainability, and testability without breaking the Arduino Uno hardware,
the Arduino IDE workflow, or the serial contract used by AI Sorter software
1.1.46 and newer.

**Implementation status:** the software work is implemented through firmware
`7.1.260714.6`. PlatformIO Uno compilation, CI, and 65 native tests pass.
Unchecked criteria below require an Arduino IDE run, physical hardware,
long-duration testing, a logic analyzer, or the Windows application; they are
not being represented as complete.

## Constraints that apply to every phase

- `ArduinoCode/CS71_Arduino/CS71_Arduino.ino` remains the canonical firmware
  and must remain directly openable, verifiable, and uploadable in Arduino IDE.
- Preserve the current Uno/CNC-shield pin mapping and 16-microstep defaults
  unless a task explicitly changes the hardware configuration.
- Treat serial command names, response text, line framing, and response timing
  as an external API. Intentional protocol changes require a firmware version
  bump, documentation, and compatibility verification with the Windows
  application.
- Store fixed serial strings with `F("...")` to protect the Uno's 2 KB SRAM.
- Native tests cover parsing, calculations, and state transitions. Motor
  direction, homing, sensor polarity, stop latency, and signal timing still
  require bench testing on representative hardware.
- Complete and validate one phase before starting a dependent phase. Do not
  combine the parser rewrite, stop redesign, and timer refactor into one patch.

## Phase 0 — Record a behavioral baseline

**Priority:** Required first

Before changing behavior, record what the current firmware and hardware do.
This gives later phases a concrete regression target instead of relying on
comments or assumptions.

### Tasks

1. Verify the unmodified sketch for an Arduino Uno in Arduino IDE.
2. Flash a bench unit with the default configuration and record:
   - startup output;
   - `version`, `getconfig`, and `ping` output byte-for-byte;
   - immediate and eventual responses to `sortto:1`, `homefeeder`,
     `homesorter`, one numeric slot command, and `xf:0`;
   - feed/sort motor direction and successful homing;
   - default `FEED_DONE_SIGNAL` timing with AirDrop disabled.
3. Repeat the relevant timing capture with `AIR_DROP_ENABLED=true` if AirDrop
   hardware is available.
4. Document the Serial Monitor settings: 9600 baud and newline-terminated
   commands.
5. Save the observations in the existing firmware documentation or a compact
   checked-in test fixture. Do not commit machine-specific COM-port names.

### Acceptance criteria

- [ ] The current sketch verifies for Arduino Uno before refactoring begins.
- [ ] Exact baseline responses exist for startup, diagnostics, configuration,
      homing, direct sort, forced feed, and a normal queued feed/sort cycle.
- [ ] Bench notes identify which checks require sensors, motors, or AirDrop.
- [ ] Any mismatch between documentation and observed firmware behavior is
      recorded before deciding which behavior is authoritative.

---

## Phase 1 — Apply isolated correctness and cleanup fixes

**Priority:** High  
**Depends on:** Phase 0

These changes are small enough to review and bench-test independently. Make
them as separate commits so a regression can be bisected.

### 1.1 Clamp camera PWM correctly

**Problem:** `adjustCameraLED()` evaluates and discards the upper-bound ternary,
so values above 255 wrap when passed to AVR `analogWrite()`.

**Implementation:**

```cpp
int clampByte(int value) {
  if (value < 0) return 0;
  if (value > 255) return 255;
  return value;
}

void adjustCameraLED(int level) {
  cameraLEDLevel = clampByte(level);
  analogWrite(CAMERA_LED_PWM, cameraLEDLevel);
}
```

Keep `clampByte()` independent of Arduino APIs so it can move into the native
logic module in Phase 2.

**Acceptance criteria:**

- [x] Native boundary tests verify values `-10`, `0`, `128`, `255`, and
      `300` result in effective levels `0`, `0`, `128`, `255`, and `255`.
- [ ] `getconfig` reports the clamped `CameraLEDLevel` when PWM support is
      compiled in.
- [ ] The non-PWM default hardware configuration is unchanged.

### 1.2 Make motor-standby parsing and arithmetic explicit

**Problem:** `autoMotorStandbyTimeout` is a signed `long`, but its setter uses
`String::toDouble()`. Multiplication by 1000 can overflow before comparison
with the `millis()` delta.

**Implementation:**

1. Define an explicit maximum timeout in seconds that can safely be converted
   to milliseconds on AVR.
2. Parse only a complete non-negative integer; do not accept a numeric prefix
   followed by arbitrary text.
3. Store the timeout as `unsigned long`.
4. Reject out-of-range input with an `error:` response instead of truncating or
   wrapping.
5. Keep the existing rollover-safe subtraction form:

```cpp
if (millis() - timeSinceLastMotorMove >= standbyTimeoutMs) {
  // ...
}
```

**Acceptance criteria:**

- [ ] `automotorstandbytimeout:0` disables standby.
- [ ] `automotorstandbytimeout:60` behaves as before.
- [x] Negative, fractional, malformed, and overflowing values return `error:`
      and leave the previous setting unchanged.
- [x] The maximum accepted value converts to milliseconds without overflow.
- [x] Behavior remains correct across a simulated `millis()` rollover.

### 1.3 Name sorter homing limits

Replace the unexplained `200` and `210` full-step limits with constants such as
`SORT_FULL_REVOLUTION_STEPS` and `SORT_HOMING_MARGIN_STEPS`. Keep the resulting
microstep counts exactly equal to the current values.

**Acceptance criteria:**

- [x] Generated search limits are unchanged for the default configuration.
- [x] The margin comment explains its hardware purpose rather than restating
      the arithmetic.

### 1.4 Remove genuinely dead code

- Remove empty `serialMessenger()` and its call from `loop()`.
- Keep `FreeMem()` temporarily for memory measurements in Phases 2 and 3.
- If `FreeMem()` becomes a public diagnostic, expose it through a separate
  `freemem` command rather than changing the existing `getconfig` schema.

**Acceptance criteria:**

- [ ] Removing `serialMessenger()` produces no serial or motor behavior change.
- [x] `FreeMem()` remains internal; no new serial response or `getconfig`
      property was added.

---

## Phase 2 — Add an optional host-test and compile workflow

**Priority:** High  
**Depends on:** Phase 1

Testing infrastructure must supplement, not replace, the documented Arduino
IDE workflow.

### Tasks

1. Add `platformio.ini` with:
   - an Arduino Uno environment that compiles the canonical sketch; and
   - a `native` environment that builds only platform-independent logic and
     tests.
2. Configure PlatformIO to use `ArduinoCode/CS71_Arduino` as the firmware source
   without moving or renaming `CS71_Arduino.ino`.
3. Add `logic.h/.cpp` beside the sketch for functions that do not call Arduino
   APIs. Start with:
   - byte clamping;
   - speed conversion;
   - checked integer parsing;
   - timeout conversion and elapsed-time checks;
   - homing-limit calculations.
4. Add Unity tests under `test/`. Do not add ArduinoFake until a test actually
   needs an Arduino API; prefer dependency-free logic.
5. Add CI jobs for native tests and Uno compilation.
6. Document both workflows:

```sh
pio run -e uno
pio test -e native
pio test -e native -f test_logic
```

Arduino IDE Verify/Upload remains the supported path for builders who do not
use PlatformIO.

### Acceptance criteria

- [x] `pio run -e uno` compiles the canonical sketch in place.
- [ ] Arduino IDE still opens and verifies the sketch without generated files,
      symlinks, or manual source copying.
- [x] `pio test -e native` runs without an attached board.
- [x] A single suite can be run with `pio test -e native -f <suite>`.
- [x] CI runs native tests and Uno compilation on every firmware change.
- [x] Compiled flash and SRAM usage are reported so later refactors cannot
      silently exceed Uno limits.

---

## Phase 3 — Replace `String` command accumulation with a bounded parser

**Priority:** High  
**Depends on:** Phase 2

### Problems to solve

- `String input` grows without a length limit if no newline arrives.
- Repeated dynamic allocation is undesirable on a long-running 2 KB SRAM
  device.
- `delay(1)` is executed for every received byte.
- Prefix parsing through `startsWith()`, `replace()`, `toInt()`, and
  `toDouble()` accepts malformed values too easily.

### Design

1. Use a fixed receive buffer sized for the longest documented command plus
   terminator.
2. Consume serial bytes without `delay(1)`.
3. Accept `\n` as the frame terminator and ignore a single preceding `\r` so
   both newline and CRLF terminals work.
4. If the line exceeds the buffer:
   - mark the frame as overflowed;
   - discard bytes through the next newline;
   - return `error:command too long`;
   - never dispatch the truncated prefix.
5. Parse command name and value without modifying the receive buffer.
6. Require complete, range-checked integer or boolean values. Reject trailing
   garbage and preserve the previous setting on error.
7. Preserve the exact existing spellings, including mixed-case
   `debounceTimeout:`/`debounceTime:` and legacy
   `airdropdsignalduration:`.
8. Preserve current successful responses, including the leading space in the
   legacy `ping` response, unless an application compatibility test authorizes
   a protocol change.
9. Characterize the current unknown-command `ok` response in Phase 0. Preserve
   it for compatibility or change it only with a documented protocol/version
   decision.

### Tests

- Every currently supported command.
- Empty line, CRLF, partial command split across reads, and multiple complete
  lines arriving in one UART burst.
- Exactly-full buffer, one-byte overflow, long overflow, and recovery on the
  next valid line.
- Missing newline without memory growth.
- Negative, maximum, overflowing, fractional, and malformed numeric values.
- Boolean values accepted by the current protocol.
- Repeated command streams with stable `FreeMem()` on a bench unit.

### Acceptance criteria

- [x] No dynamic `String` object is used by command reception or dispatch.
- [x] No per-byte `delay()` remains.
- [x] A truncated command can never execute.
- [ ] Valid command behavior and responses match the Phase 0 fixtures.
- [x] Invalid values return a deterministic `error:` response and do not
      partially update runtime state.
- [ ] Native parser tests pass and an extended bench command stream shows no
      declining free-memory trend.

---

## Phase 4 — Define and implement a real stop state

**Priority:** High for machine control; do not call it an emergency stop  
**Depends on:** Phase 3

Software `stop` improves responsiveness but is not a substitute for removing
power with a physical emergency-stop circuit. Document that distinction.

### Protocol and state decisions

Before implementation, define and document:

1. The exact successful response (`stopped` is preferred over success-shaped
   `done`).
2. Whether an already queued non-stop command is rejected, retained, or
   discarded when stopping.
3. Which commands are allowed while stopped.
4. How position is recovered after interrupting a motor between steps.

Recommended behavior:

- Serial reception runs every loop iteration, including while motors move.
- A complete `stop` frame has priority over ordinary command dispatch.
- At most one ordinary command may be pending while busy. Additional ordinary
  commands receive `error:busy`; their bytes must not be appended to an
  existing frame.
- Outside the known blocking sections listed in Phase 5, `stop` disables motor
  drivers immediately after the current step pulse, cancels feed/sort/test
  state, clears pending movement, and marks both axis positions unknown.
- During a remaining blocking `delay()`, incoming bytes stay in the UART buffer
  and cannot be processed until the delay returns. Measure and document this
  temporary worst-case latency; Phase 5 removes it.
- Movement and feed commands remain rejected until both axes complete an
  explicit recovery home. Diagnostic commands such as `version`, `getconfig`,
  and `ping` remain available.
- Successful recovery resets sorter queue positions consistently before normal
  operation resumes.

### Implementation tasks

1. Introduce an explicit top-level mode such as `Running`, `Stopped`, and
   `Recovering`; do not represent stop behavior only by manipulating a loose
   subset of booleans.
2. Centralize cancellation in `enterStoppedState()`.
3. In that function:
   - disable `MOTOR_Enable`;
   - clear feed scheduling, movement, homing, completion, and error state;
   - clear sorter movement, homing, and completion state;
   - cancel test cycles and pending completion signals;
   - force `FEED_DONE_SIGNAL` low;
   - invalidate feed and sorter positions.
4. Add a controlled recovery path that homes each axis and only then returns
   to `Running`.
5. Ensure an interrupted cycle cannot fall through `onFeedComplete()` and emit
   `done`.

### Acceptance criteria

- [ ] Outside the blocking sections listed in Phase 5, `stop` suppresses
      further step pulses after at most the current pulse plus one loop
      iteration.
- [ ] `MOTOR_Enable` is driven to its disabled state and
      `FEED_DONE_SIGNAL` is low.
- [ ] Stop during feed stepping, sort stepping, homing stepping, and test
      movement ends in the same explicit stopped state.
- [x] Phase 5 removed all runtime millisecond blocking sections; only individual
      motor-step `delayMicroseconds()` calls remain.
- [x] Native state tests and control-flow checks suppress stale `done` after
      cancellation.
- [x] Normal movement is rejected until position recovery completes.
- [ ] Recovery homes both axes, resets the sorter queue, and permits the next
      normal cycle.
- [ ] Native state tests and physical bench tests cover each interruption
      point.
- [x] Documentation states that software stop is not a certified emergency
      stop.

---

## Phase 5 — Remove long blocking operations incrementally

**Priority:** Medium  
**Depends on:** Phase 4

The current blocking inventory is broader than the two most visible delays:

- `delay(1)` in serial reception (removed in Phase 3);
- `delay(40)` in sorter test mode;
- `delay(slotDelayCalc)` before sorter movement;
- AirDrop pre-delay and signal duration;
- feed-completion notification delay;
- `delay(debounceTime)` while settling a detected case;
- the synchronous step loop in `jogSorter()`;
- per-step `delayMicroseconds()` calls.

Keep the step-pulse delays for now: they define pulse width and motor speed.
Their maximum duration becomes the expected upper bound for stop latency.
Convert the other blocking operations one subsystem at a time.

### 5.1 Slot-drop gate

Replace `delay(slotDelayCalc)` with explicit `Idle`/`WaitingDropClearance`
states. Do not update `qPos1`/`qPos2` or begin movement until the timer expires.

### 5.2 Feed completion and AirDrop

Represent completion as:

`Idle → PreSignalDelay → SignalHigh → NotificationDelay → Notify → Idle`

Set `FEED_DONE_SIGNAL` low on every cancellation and error path.

### 5.3 Sensor debounce settling

Replace `delay(debounceTime)` with a settling state and deadline. Continue
polling serial input and stop state while settling. Define whether the
proximity sensor must remain active for the entire settling period.

### 5.4 Test pacing and sorter jog

- Replace test-mode `delay(40)` with a scheduled next-action timestamp.
- Convert `jogSorter()` into an explicit movement/homing phase so startup and
  `homesorter` remain interruptible after serial initialization.

### Timing rules

- Use rollover-safe checks of the form
  `static_cast<unsigned long>(now - started) >= duration`.
- Capture `now = millis()` once per loop pass and pass it to timer updates.
- Do not promise impossible exact timing. Define tolerances from measured loop
  jitter and verify physical outputs with a logic analyzer.

### Acceptance criteria

- [x] No `delay()` remains in the runtime path.
- [x] Remaining `delayMicroseconds()` calls are limited to documented step
      pulse generation.
- [x] Serial reception and `stop` are serviced cooperatively in every software
      waiting state.
- [ ] Slot-drop, debounce, notification, and AirDrop durations remain within
      the measured and documented tolerance established on the bench.
- [ ] AirDrop produces one pulse of the configured duration and always returns
      low.
- [ ] Existing queued-sort behavior is unchanged.
- [x] Each converted subsystem has native fake-time tests before the next
      subsystem is changed.

---

## Phase 6 — Make diagnostic slot ranges explicit

**Priority:** Low  
**Depends on:** Phases 3 and 5

### Problem

`test:` previously used `random(0,6)` (slots 0–5), while `sorttest:` used
`random(0,8)` (slots 0–7). The upper bound of Arduino `random(min,max)` is
exclusive. Neither range was linked to an explicit firmware slot count, and
`SORTER_CHUTE_SEPERATION` describes distance between slots, not slot count.

### Implementation

1. Added independent `SORTER_SLOT_COUNT` and runtime `slotCount`, defaulting to
   8.
2. Added the exact checked `slotcount:` setter for manual or advanced
   configuration. It cross-validates with `sortsteps:` against AVR 16-bit
   movement arithmetic.
3. Deliberately kept `SlotCount` out of `getconfig`; Windows application
   tolerance for additional JSON properties has not been verified.
4. Both diagnostic modes now use `[0, slotCount)`.
5. Documented that slot count and mechanically compatible chute spacing are
   independent.

This intentionally expands the default feed/sort diagnostic from slots 0–5 to
0–7. Treat that as a documented behavior correction, not as “no behavior
change.”

### Acceptance criteria

- [x] Both diagnostic modes select only values in `[0, slotCount)`.
- [x] Default diagnostics can reach all eight standard slots.
- [x] Slot count and chute separation remain independent settings.
- [x] Invalid values (`0`, negative, or above the AVR-safe limit for the
      current geometry) are rejected without changing the active value.
- [x] The `getconfig` schema remains unchanged pending application
      compatibility verification.

Native logic tests and an Uno compile cover the arithmetic and build. Physical
slot reachability, motor direction, homing, and application-driven advanced
configuration still require hardware/application validation.

---

## Phase 7 — Documentation, compatibility, and release

**Priority:** Required for completion  
**Depends on:** All implemented phases

### Tasks

1. Update `FIRMWARE_VERSION` and the version banner together whenever protocol
   behavior or required AI Sorter compatibility changes.
2. Update firmware documentation with:
   - Arduino IDE compile/upload instructions;
   - PlatformIO build and native-test commands;
   - how to run one test suite;
   - exact serial commands and responses;
   - stopped/recovery behavior;
   - configurable limits and defaults;
   - the distinction between software stop and physical power isolation.
3. Keep community firmware variants unchanged unless their maintainers
   explicitly opt into the canonical changes.
4. Run the full native suite, Uno compile, flash/boot smoke test, and the Phase
   0 bench matrix.
5. Compare flash/SRAM usage with the baseline and investigate material growth.

### Release acceptance criteria

- [x] PlatformIO compiles the canonical Uno firmware; Arduino IDE verification
      remains a manual release check.
- [x] Native tests and CI pass.
- [ ] Startup, serial protocol, queued sorting, homing, and default hardware
      behavior pass regression checks.
- [ ] Stop and recovery pass bench tests at every documented interruption
      point.
- [ ] AirDrop and PWM variants are tested when their code paths changed.
- [x] Firmware/application compatibility boundaries and intentional protocol changes
      are documented in release notes.

## Dependency order

1. Record the baseline.
2. Land isolated fixes.
3. Add optional native tests and Uno CI compilation.
4. Replace serial reception and parsing.
5. Implement explicit stop and recovery semantics.
6. Convert blocking waits incrementally.
7. Correct diagnostic slot configuration.
8. Update documentation and perform the release regression matrix.

Do not start with a wholesale PlatformIO migration or a single large
state-machine rewrite. The plan is deliberately sequenced so each risky change
has a known baseline, focused tests, and a reversible review boundary.

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
```

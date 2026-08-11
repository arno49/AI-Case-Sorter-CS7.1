# cs71-protocol

`cs71_protocol` is a Python 3.10+ host library for the CS7.1 v1/v2 serial
protocol. It has no runtime dependency for injected byte streams. Install
`cs71-protocol[serial]` only to use `SerialTransport.open()` with pyserial.

## Public API

- `ProtocolClient`: exact v1 discovery/activation, correlated v2 requests,
  CRC transitions, event-gap tracking, typed `get_status`,
  `get_capabilities`, and `get_queue`, plus fail-closed recovery. Long-running
  requests accept a bounded interrupt predicate; interruption succeeds only
  after the same owner receives the exact out-of-band `stopped` terminal.
- `LineReader` and `ByteStream`: bounded LF/CRLF framing that never trims a
  protocol line; v2 enforces printable ASCII and 64-byte frames. Injected
  streams must honor `read(size, timeout=...)` so read deadlines remain bounded.
- `classify_v1_response`, `classify_discovery`, and `parse_v2_line`: strict
  response classifiers; notably, legacy ` ok` is distinct from `ok`.
- `crc16_ccitt_false`, `append_crc`, and `remove_and_verify_crc`: independent
  CRC-16/CCITT-FALSE helpers.
- `Response`, `Event`, `Status`, `Capabilities`, `QueueSnapshot`, `Fault`, and
  `Completion`: typed wire values. Unknown `key=value` fields are retained in
  `extras` where applicable.
- `ScriptedTransport`: deterministic serial mock for tests; `SerialTransport`
  is the optional real-port adapter.

```python
from cs71_protocol import ProtocolClient, SerialTransport

client = ProtocolClient(SerialTransport.open("/dev/ttyACM0"))
client.wait_ready()
client.v1_ping_barrier()
if client.activate():
    status = client.get_status()
```

## No-hardware scope

This package validates protocol framing and specified fixture interactions only.
It does **not** establish motor safety, physical completion timing, serial DTR
behavior on every platform, hardware qualification, or parity with real
firmware. Those remain explicit V2-11/HIL/Windows release gates.

## Development checks

From this directory, install the development extra, run the complete no-hardware
suite, and build the distributable wheel and source archive:

```sh
python -m pip install ".[dev]" "build==1.2.2.post1"
python -m pytest
python -m build --wheel --sdist
```

## Compatibility switcher CLI

Install with `pip install .[serial]`, then use the packaged `cs71-protocol`
command. On supported backends it opens a real port at 9600 8N1, takes both a
process lock and pyserial's exclusive handle where available, configures DTR
low before opening, and releases both before launching a legacy application.

```text
cs71-protocol [--json] detect --port /dev/ttyACM0 [--no-reset]
cs71-protocol [--json] enter-v2 --port /dev/ttyACM0 [--no-reset] [--crc]
cs71-protocol [--json] leave-v2 --port /dev/ttyACM0
cs71-protocol [--json] prepare-legacy --port /dev/ttyACM0
cs71-protocol [--json] run-legacy --port /dev/ttyACM0 -- <application> [arguments...]
```

Every command accepts a finite, positive `--timeout SECONDS` (default `1`) and
`--json` may also follow the subcommand. `detect` never activates after legacy
`ok`; `leave-v2` uses safe stop/reset fallback because a new CLI process cannot
know v2/CRC state. `prepare-legacy` and `run-legacy` reset volatile controller
settings. JSON is the sole stdout content when requested, including usage and
runtime errors; human errors go to stderr.

The port is constructed with `port=None`, DTR is set low, and only then is the
port assigned and opened. This is claimed as a pre-open physical DTR guarantee
**only** for pyserial's `serial.serialwin32` backend on `win32`; the injectable
`supports_preopen_dtr_suppression` predicate makes that boundary testable.
POSIX and macOS are deliberately rejected with exit 6 before opening a real
port or claiming that a pre-reset stop was safe. The post-open `raw.dtr` value
is not used as proof because it is only pyserial's cached configuration.

`--no-reset` remains accepted by the parser for compatibility, but is rejected
with exit 6 for `detect` and `enter-v2`: universal `stop` preserves v2 and
cannot produce the v1 `Ready` barrier, so v1 cannot be verified without reset.

| Exit | Meaning |
| ---: | --- |
| 0 | success |
| 2 | command-line usage |
| 3 | port unavailable or contended |
| 4 | protocol violation or timeout, including an operation that failed after verified v1 recovery |
| 5 | recovery could not establish verified v1 (session remains uncertain) |
| 6 | required DTR/safety guarantee could not be established |
| 7 | legacy child could not launch |

`run-legacy` otherwise returns the launched child's exit status unchanged in
both human and JSON modes. Child statuses can therefore collide with the stable
tool exit values above by design; use `child_exit` in JSON to distinguish them.
Physical DTR/reset behavior for the supported Windows backend still requires
HIL qualification; unsupported backends are never represented as guaranteed.

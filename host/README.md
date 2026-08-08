# cs71-protocol

`cs71_protocol` is a Python 3.10+ host library for the CS7.1 v1/v2 serial
protocol. It has no runtime dependency for injected byte streams. Install
`cs71-protocol[serial]` only to use `SerialTransport.open()` with pyserial.

## Public API

- `ProtocolClient`: exact v1 discovery/activation, correlated v2 requests,
  CRC transitions, event-gap tracking, typed `get_status`,
  `get_capabilities`, and `get_queue`, plus fail-closed recovery.
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

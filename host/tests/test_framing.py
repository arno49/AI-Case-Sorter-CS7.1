import pytest
from time import monotonic, sleep

from cs71_protocol import FramingError, LineReader, ScriptedTransport, SerialTransport, TimeoutError
from cs71_protocol import framing


def test_lf_crlf_and_legacy_leading_space_are_preserved():
    reader = LineReader(ScriptedTransport([b" ok\r\nReady\n"]))
    assert reader.read_line(.02, v2=False) == " ok"
    assert reader.read_line(.02, v2=False) == "Ready"


def test_v2_rejects_overlong_invalid_and_bare_cr_then_recovers():
    reader = LineReader(ScriptedTransport([b"x" * 64 + b"\r\n"]))
    assert reader.read_line(.02, v2=True) == "x" * 64

    reader = LineReader(ScriptedTransport([b"x" * 65 + b"\n@1 done\n"]))
    with pytest.raises(FramingError, match="exceeds 64"):
        reader.read_line(.02, v2=True)
    assert reader.read_line(.02, v2=True) == "@1 done"

    reader = LineReader(ScriptedTransport([b"@1 d\x01ne\n", b"@1 done\r\n"]))
    with pytest.raises(FramingError, match="non-printable"):
        reader.read_line(.02, v2=True)
    assert reader.read_line(.02, v2=True) == "@1 done"

    reader = LineReader(ScriptedTransport([b"@1 d\rone\n"]))
    with pytest.raises(FramingError, match="bare CR"):
        reader.read_line(.02, v2=True)


def test_rejected_partial_frame_is_discarded_across_timeouts():
    stream = ScriptedTransport([b"@1 d\x01"])
    reader = LineReader(stream)
    with pytest.raises(TimeoutError):
        reader.read_line(.002, v2=True)
    stream.incoming.append(b"one\n@1 done\n")
    with pytest.raises(FramingError, match="non-printable"):
        reader.read_line(.02, v2=True)
    assert reader.read_line(.02, v2=True) == "@1 done"


def test_reader_checks_deadline_before_another_continuous_byte_read(monkeypatch):
    class OneByteThenFail:
        def __init__(self) -> None:
            self.reads = 0

        def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
            self.reads += 1
            if self.reads == 1:
                return b"x"
            raise AssertionError("read occurred after the deadline")

        def write(self, data: bytes) -> int:
            return len(data)

    ticks = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr(framing, "monotonic", lambda: next(ticks))
    stream = OneByteThenFail()

    with pytest.raises(TimeoutError):
        LineReader(stream).read_line(.5, v2=True)
    assert stream.reads == 1


def test_serial_read_is_capped_to_the_remaining_line_deadline():
    class BlockingSerial:
        def __init__(self) -> None:
            self.timeout = .05
            self.read_timeouts: list[float] = []

        def read(self, size: int) -> bytes:
            self.read_timeouts.append(self.timeout)
            sleep(self.timeout)
            return b""

    serial = BlockingSerial()
    start = monotonic()

    with pytest.raises(TimeoutError):
        LineReader(SerialTransport(serial)).read_line(.001, v2=True)

    assert serial.read_timeouts and serial.read_timeouts[0] <= .001
    assert monotonic() - start < .04
    assert serial.timeout == .05

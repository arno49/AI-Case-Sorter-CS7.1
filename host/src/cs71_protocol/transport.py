"""Transport adapters; pyserial is imported only when opening a real port."""

from __future__ import annotations

from collections import deque
from typing import Iterable

from .framing import ByteStream


class SerialTransport:
    """Small adapter around a pyserial object."""

    def __init__(self, serial: object) -> None:
        self.serial = serial

    @classmethod
    def open(cls, port: str, *, baudrate: int = 9600, timeout: float = 0.05) -> "SerialTransport":
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Install cs71-protocol[serial] to open serial ports") from exc
        return cls(serial.Serial(port=port, baudrate=baudrate, bytesize=8, parity="N", stopbits=1,
                                 timeout=timeout, xonxoff=False, rtscts=False, dsrdtr=False))

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        if timeout is None:
            return self.serial.read(size)  # type: ignore[no-any-return]
        previous_timeout = self.serial.timeout
        limited_timeout = timeout if previous_timeout is None else min(previous_timeout, timeout)
        try:
            self.serial.timeout = limited_timeout
            return self.serial.read(size)  # type: ignore[no-any-return]
        finally:
            self.serial.timeout = previous_timeout

    def write(self, data: bytes) -> int:
        return self.serial.write(data)  # type: ignore[no-any-return]

    def reset(self) -> None:
        """Request a DTR reset where the underlying serial adapter supports it."""
        self.serial.dtr = False
        self.serial.dtr = True

    def close(self) -> None:
        self.serial.close()


class ScriptedTransport:
    """Deterministic no-hardware byte stream useful for tests and fixture replay."""

    def __init__(self, incoming: Iterable[bytes | str] = (), *, reset_incoming: Iterable[bytes | str] = ()) -> None:
        self.incoming = deque(item.encode("ascii") if isinstance(item, str) else item for item in incoming)
        self.reset_incoming = tuple(item.encode("ascii") if isinstance(item, str) else item for item in reset_incoming)
        self.writes: list[bytes] = []
        self.resets = 0

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        if not self.incoming:
            return b""
        item = self.incoming.popleft()
        if len(item) > size:
            self.incoming.appendleft(item[size:])
            return item[:size]
        return item

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def reset(self) -> None:
        self.resets += 1
        self.incoming.extend(self.reset_incoming)

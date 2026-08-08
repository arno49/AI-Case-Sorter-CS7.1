"""Bounded LF/CRLF framing without protocol-line trimming."""

from __future__ import annotations

from time import monotonic, sleep
from typing import Protocol, runtime_checkable

from .errors import FramingError, TimeoutError


@runtime_checkable
class ByteStream(Protocol):
    """Minimal byte transport used by the protocol library and tests."""

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        """Return up to *size* bytes, respecting *timeout* when it is provided."""

    def write(self, data: bytes) -> int | None:
        """Write bytes to the transport."""


class LineReader:
    """Read complete protocol lines while preserving every non-terminator byte."""

    def __init__(self, stream: ByteStream, *, v1_limit: int = 4096, v2_limit: int = 64) -> None:
        self.stream = stream
        self.v1_limit = v1_limit
        self.v2_limit = v2_limit
        self._pending = bytearray()
        self._line = bytearray()
        self._discarded_reason: str | None = None

    def read_line(self, timeout: float, *, v2: bool) -> str:
        """Read one LF/CRLF line; v2 rejects non-printable and overlong frames."""
        deadline = monotonic() + timeout
        limit = self.v2_limit if v2 else self.v1_limit
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for a complete line")
            if self._pending:
                chunk = bytes(self._pending[:1])
                del self._pending[:1]
            else:
                chunk = self.stream.read(1, timeout=remaining)
            if not chunk:
                sleep(min(0.001, max(0, deadline - monotonic())))
                continue
            for byte in chunk:
                if byte == 0x0A:
                    if self._discarded_reason:
                        reason = self._discarded_reason
                        self._line.clear()
                        self._discarded_reason = None
                        raise FramingError(reason)
                    payload = bytes(self._line)
                    self._line.clear()
                    if payload.endswith(b"\r"):
                        payload = payload[:-1]
                    try:
                        return payload.decode("ascii")
                    except UnicodeDecodeError as exc:
                        raise FramingError("non-ASCII protocol line") from exc
                if self._discarded_reason is None:
                    if v2 and self._line.endswith(b"\r"):
                        self._discarded_reason = "bare CR in v2 line"
                    elif v2 and byte != 0x0D and (byte < 0x20 or byte > 0x7E):
                        self._discarded_reason = "invalid non-printable v2 byte"
                    elif len(self._line) >= limit and (not v2 or byte != 0x0D):
                        self._discarded_reason = f"line exceeds {limit} bytes"
                    else:
                        self._line.append(byte)
            # Keep reading until LF after a rejected frame.

    def clear(self) -> None:
        """Discard partial local framing state after a transport reset."""
        self._pending.clear()
        self._line = bytearray()
        self._discarded_reason = None

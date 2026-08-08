"""Transport adapters; pyserial is imported only when opening a real port."""

from __future__ import annotations

from collections import deque
import sys
from collections.abc import Callable, Iterable

from .errors import DtrSuppressionError
from .framing import ByteStream


def supports_preopen_dtr_suppression(raw: object, *, platform: str | None = None) -> bool:
    """Return whether this backend has the documented pre-open DTR path.

    pyserial's Windows backend applies its configured DTR state while opening.
    Other backends are deliberately not treated as a physical-reset guarantee.
    ``platform`` exists to make the predicate independently testable.
    """
    return (sys.platform if platform is None else platform) == "win32" and (
        type(raw).__module__ == "serial.serialwin32"
    )


class SerialTransport:
    """Small adapter around a pyserial object."""

    def __init__(self, serial: object, *, dtr_suppression_guaranteed: bool = False) -> None:
        self.serial = serial
        self.dtr_suppression_guaranteed = dtr_suppression_guaranteed

    @classmethod
    def open(
        cls,
        port: str,
        *,
        baudrate: int = 9600,
        timeout: float = 0.05,
        dtr_suppression_supported: Callable[[object], bool] = supports_preopen_dtr_suppression,
    ) -> "SerialTransport":
        try:
            import serial  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("Install cs71-protocol[serial] to open serial ports") from exc
        # Construct closed and configure DTR before assigning a port. Passing
        # ``exclusive=True`` asks pyserial to take an exclusive handle where it
        # is supported; the CLI also takes a portable process lock.
        try:
            raw = serial.Serial(port=None, baudrate=baudrate, bytesize=8, parity="N", stopbits=1,
                                timeout=timeout, xonxoff=False, rtscts=False, dsrdtr=False,
                                exclusive=True)
        except TypeError:
            # Older pyserial/platform backends may not expose ``exclusive``.
            raw = serial.Serial(port=None, baudrate=baudrate, bytesize=8, parity="N", stopbits=1,
                                timeout=timeout, xonxoff=False, rtscts=False, dsrdtr=False)
        try:
            raw.dtr = False
        except Exception as exc:
            raise DtrSuppressionError("serial driver cannot suppress DTR before opening the port") from exc
        if not dtr_suppression_supported(raw):
            try:
                raw.close()
            except Exception:
                pass
            raise DtrSuppressionError(
                "pre-open DTR suppression is only guaranteed for the Windows pyserial backend"
            )
        try:
            raw.port = port
            raw.open()
        except Exception:
            if getattr(raw, "is_open", False):
                raw.close()
            raise
        # Do not read ``raw.dtr`` here: it is a pyserial cached configuration
        # value, not evidence of what happened electrically during open.
        return cls(raw, dtr_suppression_guaranteed=True)

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
        self.closed = False
        self.dtr_suppression_guaranteed = True

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

    def close(self) -> None:
        self.closed = True

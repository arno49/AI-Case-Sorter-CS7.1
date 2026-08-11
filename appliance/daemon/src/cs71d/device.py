"""Device transport policy for the daemon's single serial owner.

Opening a real controller port is gated on DTR qualification. Pre-open DTR
suppression is only guaranteed by the Windows pyserial backend; Linux and other
POSIX behaviour is recorded as ``NOT_EXECUTED`` in the architecture's hardware
gates. Until that gate is legitimately closed with hardware evidence, this
module refuses to open a real port on POSIX rather than opening one and hoping
the controller does not reset.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol

from cs71_protocol import ByteStream

from .config import Backend, DaemonConfig
from .simulator import SimulatorTransport

#: The Linux/Raspberry Pi DTR qualification gate has not been executed.
DTR_GATE_STATUS = "NOT_EXECUTED"


class OwnedByteStream(ByteStream, Protocol):
    """Byte stream whose lifecycle is owned by the serial worker."""

    def close(self) -> None:
        """Release the transport."""


class DtrGateError(RuntimeError):
    """Opening a real serial device is blocked by the unqualified DTR gate."""


class DevicePolicyError(RuntimeError):
    """The configuration cannot be turned into a usable transport."""


def create_transport_factory(
    config: DaemonConfig,
    *,
    platform: str | None = None,
) -> Callable[[], OwnedByteStream]:
    """Return a factory the serial worker thread calls to open its transport.

    The factory is deliberately not invoked here: the worker owns construction
    so that every byte of controller I/O happens on the owning thread.
    """
    if config.backend is Backend.SIMULATOR:
        return SimulatorTransport

    device_path = config.device_path
    if device_path is None:
        raise DevicePolicyError("serial backend requires a device_path")

    effective_platform = sys.platform if platform is None else platform
    if effective_platform != "win32":
        raise DtrGateError(
            f"real serial open is blocked on {effective_platform}: pre-open DTR "
            f"suppression is unqualified and the DTR gate is {DTR_GATE_STATUS}"
        )

    def factory() -> OwnedByteStream:
        from cs71_protocol import SerialTransport

        transport = SerialTransport.open(device_path, baudrate=9600, timeout=0.05)
        if not transport.dtr_suppression_guaranteed:
            transport.close()
            raise DtrGateError("serial driver could not guarantee DTR suppression during open")
        return transport

    return factory

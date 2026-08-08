"""Protocol-specific exceptions."""


class ProtocolError(Exception):
    """Base class for an invalid or unsafe protocol interaction."""


class FramingError(ProtocolError):
    """A received line violates transport framing."""


class ParseError(ProtocolError):
    """A syntactically invalid protocol frame was received."""


class CrcError(ParseError):
    """A protected frame has a missing or incorrect CRC."""


class TimeoutError(ProtocolError):
    """A protocol response was not received before its deadline."""


class RecoveryError(ProtocolError):
    """A recovery operation failed, optionally after verified v1 recovery."""

    def __init__(self, message: str, *, recovered: bool = False) -> None:
        super().__init__(message)
        self.recovered = recovered


class DtrSuppressionError(ProtocolError):
    """The serial driver cannot confirm DTR was suppressed during open."""


class RequestStateError(ProtocolError):
    """A response violates request lifecycle or correlation rules."""

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
    """The session became uncertain and could not be verified as v1."""


class RequestStateError(ProtocolError):
    """A response violates request lifecycle or correlation rules."""

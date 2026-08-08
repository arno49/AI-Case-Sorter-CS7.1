"""Independent CRC-16/CCITT-FALSE implementation."""

from .errors import CrcError, ParseError


def crc16_ccitt_false(data: bytes) -> int:
    """Return CRC-16/CCITT-FALSE (poly 1021, init FFFF, no reflection)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def format_crc(data: bytes) -> str:
    """Format a CRC as the protocol's four uppercase hexadecimal digits."""
    return f"{crc16_ccitt_false(data):04X}"


def append_crc(frame: str) -> str:
    """Append a CRC suffix to printable ASCII frame content."""
    try:
        encoded = frame.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ParseError("CRC frame must be ASCII") from exc
    return f"{frame}*{format_crc(encoded)}"


def remove_and_verify_crc(frame: str, *, required: bool) -> str:
    """Return the unprotected frame after enforcing the current CRC boundary."""
    if "*" not in frame:
        if required:
            raise CrcError("CRC required but absent")
        return frame
    if not required:
        raise CrcError("CRC present while CRC is disabled")
    body, separator, supplied = frame.rpartition("*")
    if not separator or len(supplied) != 4 or any(c not in "0123456789ABCDEF" for c in supplied):
        raise CrcError("invalid CRC suffix")
    try:
        expected = format_crc(body.encode("ascii"))
    except UnicodeEncodeError as exc:
        raise CrcError("CRC frame must be ASCII") from exc
    if supplied != expected:
        raise CrcError(f"bad CRC: expected {expected}, got {supplied}")
    return body

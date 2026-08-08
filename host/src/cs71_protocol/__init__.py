"""Public API for the dependency-light CS7.1 protocol host library."""

from .client import ProtocolClient
from .crc import append_crc, crc16_ccitt_false, format_crc, remove_and_verify_crc
from .errors import (CrcError, FramingError, ParseError, ProtocolError, RecoveryError,
                     RequestStateError, TimeoutError)
from .framing import ByteStream, LineReader
from .models import (Capabilities, Completion, Event, EventKind, Fault, QueueSnapshot, Response,
                     ResponseKind, SessionMode, Status)
from .parsing import (DiscoveryReply, V1ResponseKind, classify_discovery, classify_v1_response,
                      format_request, parse_v2_line)
from .tracking import EventTracker, RequestTracker
from .transport import ScriptedTransport, SerialTransport

__all__ = [
    "ByteStream", "Capabilities", "Completion", "CrcError", "DiscoveryReply", "Event", "EventKind",
    "EventTracker", "Fault", "FramingError", "LineReader", "ParseError", "ProtocolClient",
    "ProtocolError", "QueueSnapshot", "RecoveryError", "RequestStateError", "RequestTracker",
    "Response", "ResponseKind", "ScriptedTransport", "SerialTransport", "SessionMode", "Status",
    "TimeoutError", "V1ResponseKind", "append_crc", "classify_discovery", "classify_v1_response",
    "crc16_ccitt_false", "format_crc", "format_request", "parse_v2_line", "remove_and_verify_crc",
]

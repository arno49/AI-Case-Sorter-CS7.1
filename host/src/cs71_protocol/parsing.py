"""Strict parsers and formatters for v1 discovery and v2 frames."""

from __future__ import annotations

import re
import json
from enum import Enum
from typing import Mapping

from .crc import append_crc, remove_and_verify_crc
from .errors import ParseError
from .models import Event, EventKind, Response, ResponseKind, uint

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")


class DiscoveryReply(str, Enum):
    AVAILABLE = "available"
    LEGACY = "legacy"


class V1ResponseKind(str, Enum):
    """Classifications for legacy lines, without changing their bytes."""

    READY = "ready"
    OK = "ok"
    PING = "ping"
    DONE = "done"
    STOPPED = "stopped"
    WAITING = "waiting"
    ERROR = "error"
    CONFIG = "config"
    UNKNOWN = "unknown"


def classify_v1_response(line: str) -> V1ResponseKind:
    """Classify a v1 response without stripping its significant leading space."""
    exact = {
        "Ready": V1ResponseKind.READY,
        "ok": V1ResponseKind.OK,
        " ok": V1ResponseKind.PING,
        "done": V1ResponseKind.DONE,
        "stopped": V1ResponseKind.STOPPED,
        "waiting for brass": V1ResponseKind.WAITING,
    }
    if line in exact:
        return exact[line]
    if line.startswith("error:"):
        return V1ResponseKind.ERROR
    if line.startswith("{") and line.endswith("}"):
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError:
            return V1ResponseKind.UNKNOWN
        if isinstance(decoded, dict):
            return V1ResponseKind.CONFIG
    return V1ResponseKind.UNKNOWN


def classify_discovery(line: str) -> DiscoveryReply:
    """Classify only the two exact safe discovery replies."""
    if line == "protocol:2 available":
        return DiscoveryReply.AVAILABLE
    if line == "ok":
        return DiscoveryReply.LEGACY
    raise ParseError(f"unsafe discovery response: {line!r}")


def parse_fields(text: str) -> Mapping[str, str]:
    if not text:
        return {}
    fields: dict[str, str] = {}
    for token in text.split(" "):
        if token.count("=") != 1:
            raise ParseError("expected key=value fields")
        key, value = token.split("=", 1)
        if not _NAME.fullmatch(key) or not value or any(char.isspace() for char in value):
            raise ParseError("invalid key=value field")
        if key in fields:
            raise ParseError(f"duplicate field {key}")
        fields[key] = value
    return fields


def _error(text: str) -> tuple[int, str, Mapping[str, str]]:
    parts = text.split(" ", 1)
    detail = parts[0].split(":")
    if len(detail) != 3 or detail[0] not in {"error", "fault", "reject"} or not _NAME.fullmatch(detail[2]):
        raise ParseError("invalid error/fault form")
    return uint(detail[1], "error code", minimum=1, maximum=65535), detail[2], parse_fields(parts[1] if len(parts) == 2 else "")


def _response_body(request_id: int, body: str) -> Response:
    if body == "accepted":
        return Response(request_id, ResponseKind.ACCEPTED)
    if body.startswith("accepted:"):
        return Response(request_id, ResponseKind.ACCEPTED, _nonempty_fields(body[9:]))
    for prefix, kind in (("progress:", ResponseKind.PROGRESS), ("data:", ResponseKind.DATA),
                         ("done:", ResponseKind.DONE)):
        if body.startswith(prefix):
            return Response(request_id, kind, _nonempty_fields(body[len(prefix):]))
    if body == "done":
        return Response(request_id, ResponseKind.DONE)
    if body.startswith("error:"):
        code, name, fields = _error(body)
        return Response(request_id, ResponseKind.ERROR, fields, code, name)
    raise ParseError(f"unknown v2 response terminal/form: {body!r}")


def _nonempty_fields(text: str) -> Mapping[str, str]:
    if not text:
        raise ParseError("expected non-empty key=value fields")
    return parse_fields(text)


def parse_v2_line(line: str, *, crc_required: bool = False) -> Response | Event | str:
    """Parse a v2 response/event, or the sole permitted unframed ``stopped`` line."""
    try:
        encoded = line.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ParseError("invalid v2 frame bytes") from exc
    if not line or len(encoded) > 64 or any(not (0x20 <= ord(c) <= 0x7E) for c in line):
        raise ParseError("invalid v2 frame bytes")
    if line == "stopped":
        return line
    body = remove_and_verify_crc(line, required=crc_required)
    if body.startswith("@"):
        match = re.fullmatch(r"@([0-9]+) (.+)", body)
        if not match:
            raise ParseError("invalid correlated response")
        return _response_body(uint(match.group(1), "response id", maximum=65535), match.group(2))
    if body.startswith("!"):
        match = re.fullmatch(r"!([0-9]+) (.+)", body)
        if not match:
            raise ParseError("invalid event")
        sequence = uint(match.group(1), "event sequence", minimum=1, maximum=65535)
        event_body = match.group(2)
        if event_body.startswith("state:"):
            return Event(sequence, EventKind.STATE, _nonempty_fields(event_body[6:]))
        if event_body.startswith("fault:") or event_body.startswith("reject:"):
            code, name, fields = _error(event_body)
            return Event(sequence, EventKind.FAULT if event_body.startswith("fault:") else EventKind.REJECT,
                         fields, code, name)
    raise ParseError(f"unknown v2 frame form: {body!r}")


def format_request(request_id: int, command: str, *, crc: bool = False) -> str:
    """Format a validated correlated v2 request."""
    if not 1 <= request_id <= 65535:
        raise ParseError("request ID must be 1..65535")
    if (not command or command != command.strip() or "*" in command
            or any(not (0x20 <= ord(character) <= 0x7E) for character in command)):
        raise ParseError("invalid command whitespace")
    frame = f"@{request_id} {command}"
    try:
        frame.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ParseError("v2 request must be ASCII") from exc
    formatted = append_crc(frame) if crc else frame
    if len(formatted.encode("ascii")) > 64:
        raise ParseError("v2 request exceeds 64 bytes")
    return formatted

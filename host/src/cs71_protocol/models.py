"""Typed values exposed by the CS7.1 v2 protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import re
from typing import Mapping

from .errors import ParseError

_UINT = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def uint(value: str, name: str, *, minimum: int = 0, maximum: int | None = None) -> int:
    if not _UINT.fullmatch(value):
        raise ParseError(f"{name} must be an unsigned decimal integer")
    result = int(value)
    if result < minimum or (maximum is not None and result > maximum):
        raise ParseError(f"{name} is out of range")
    return result


def bit(value: str, name: str) -> bool:
    if value not in ("0", "1"):
        raise ParseError(f"{name} must be 0 or 1")
    return value == "1"


class SessionMode(str, Enum):
    UNCERTAIN = "uncertain"
    V1 = "v1"
    V2 = "v2"


class ResponseKind(str, Enum):
    ACCEPTED = "accepted"
    PROGRESS = "progress"
    DATA = "data"
    DONE = "done"
    ERROR = "error"


class EventKind(str, Enum):
    STATE = "state"
    FAULT = "fault"
    REJECT = "reject"


@dataclass(frozen=True)
class Response:
    request_id: int
    kind: ResponseKind
    fields: Mapping[str, str] = field(default_factory=dict)
    code: int | None = None
    name: str | None = None

    @property
    def terminal(self) -> bool:
        return self.kind in (ResponseKind.DONE, ResponseKind.ERROR)


@dataclass(frozen=True)
class Event:
    sequence: int
    kind: EventKind
    fields: Mapping[str, str] = field(default_factory=dict)
    code: int | None = None
    name: str | None = None


@dataclass(frozen=True)
class Completion:
    request_id: int
    succeeded: bool
    fields: Mapping[str, str]
    responses: tuple[Response, ...]
    error: "Fault | None" = None

    @property
    def terminal_response(self) -> Response:
        """Return the correlated terminal response that completed this request."""
        response = self.responses[-1]
        if not response.terminal:
            raise ParseError("completion does not end in a terminal response")
        return response

    @property
    def terminal_fields(self) -> Mapping[str, str]:
        """Return fields carried by the terminal response only."""
        return self.terminal_response.fields


@dataclass(frozen=True)
class Fault:
    code: int
    name: str
    fields: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_response(cls, response: Response) -> "Fault":
        if response.kind is not ResponseKind.ERROR or response.code is None or response.name is None:
            raise ParseError("response is not a fault/error")
        return cls(response.code, response.name, response.fields)

    @classmethod
    def from_event(cls, event: Event) -> "Fault":
        if event.kind is not EventKind.FAULT or event.code is None or event.name is None:
            raise ParseError("event is not a fault")
        return cls(event.code, event.name, event.fields)


@dataclass(frozen=True)
class Status:
    mode: str
    phase: str
    feed_homed: bool
    sort_homed: bool
    motor_enabled: bool
    active_id: int | None
    fault_code: int
    queue_previous: int
    queue_next: int
    config_generation: int
    extras: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> "Status":
        required = {"mode", "phase", "feed_homed", "sort_homed", "motor_enabled", "active_id",
                    "fault_code", "queue_previous", "queue_next", "config_generation"}
        missing = required - fields.keys()
        if missing:
            raise ParseError(f"status missing required fields: {', '.join(sorted(missing))}")
        if fields["mode"] not in {"running", "recovering", "stopped"}:
            raise ParseError("invalid status mode")
        if fields["phase"] not in {"idle", "feed_wait", "feed_move", "feed_home", "sort_move",
                                   "sort_home", "settling", "airdrop", "diagnostic"}:
            raise ParseError("invalid status phase")
        active = None if fields["active_id"] == "none" else uint(fields["active_id"], "active_id", maximum=65535)
        return cls(fields["mode"], fields["phase"], bit(fields["feed_homed"], "feed_homed"),
                   bit(fields["sort_homed"], "sort_homed"), bit(fields["motor_enabled"], "motor_enabled"),
                   active, uint(fields["fault_code"], "fault_code"), uint(fields["queue_previous"], "queue_previous"),
                   uint(fields["queue_next"], "queue_next"), uint(fields["config_generation"], "config_generation"),
                   {key: value for key, value in fields.items() if key not in required})


@dataclass(frozen=True)
class Capabilities:
    protocol: int
    max_line: int
    crc: str
    queue_depth: int
    slot_max: int
    slot_count: int
    pwm: bool
    airdrop: bool
    feed_sensor: bool
    feed_home: bool
    sort_home: bool
    extras: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> "Capabilities":
        required = {"protocol", "max_line", "crc", "queue_depth", "slot_max", "slot_count", "pwm",
                    "airdrop", "feed_sensor", "feed_home", "sort_home"}
        missing = required - fields.keys()
        if missing:
            raise ParseError(f"capabilities missing required fields: {', '.join(sorted(missing))}")
        protocol = uint(fields["protocol"], "protocol")
        if protocol != 2 or uint(fields["max_line"], "max_line") != 64 or fields["crc"] not in {"none", "optional"}:
            raise ParseError("invalid required capability value")
        return cls(protocol, 64, fields["crc"], uint(fields["queue_depth"], "queue_depth", minimum=1),
                   uint(fields["slot_max"], "slot_max"), uint(fields["slot_count"], "slot_count"),
                   bit(fields["pwm"], "pwm"), bit(fields["airdrop"], "airdrop"),
                   bit(fields["feed_sensor"], "feed_sensor"), bit(fields["feed_home"], "feed_home"),
                   bit(fields["sort_home"], "sort_home"),
                   {key: value for key, value in fields.items() if key not in required})


@dataclass(frozen=True)
class QueueSnapshot:
    queue_depth: int
    queue_previous: int
    queue_next: int
    extras: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_fields(cls, fields: Mapping[str, str]) -> "QueueSnapshot":
        required = {"queue_depth", "queue_previous", "queue_next"}
        if missing := required - fields.keys():
            raise ParseError(f"queue missing required fields: {', '.join(sorted(missing))}")
        return cls(uint(fields["queue_depth"], "queue_depth", minimum=1),
                   uint(fields["queue_previous"], "queue_previous"), uint(fields["queue_next"], "queue_next"),
                   {key: value for key, value in fields.items() if key not in required})

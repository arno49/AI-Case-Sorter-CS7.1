"""Byte-exact loaders and transport for normative protocol fixture replay."""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_HEX = re.compile(r"[0-9A-Fa-f]{2}\Z")


class ReplayAction(StrEnum):
    RESET = "reset"
    WRITE = "write"


@dataclass(frozen=True, slots=True)
class FixtureExchange:
    section: str
    request: bytes | None
    request_marker: str | None
    response: bytes | None
    response_marker: str | None = None

    @property
    def replayable(self) -> bool:
        has_action = self.request is not None or self.request_marker == "<reset>"
        return has_action and self.response is not None

    def to_replay_step(self) -> ReplayStep:
        if not self.replayable:
            marker = self.request_marker or self.response_marker
            raise ValueError(f"symbolic fixture exchange is not replayable: {marker}")
        assert self.response is not None
        action = ReplayAction.RESET if self.request_marker == "<reset>" else ReplayAction.WRITE
        return ReplayStep(action, self.request, (self.response,))


@dataclass(frozen=True, slots=True)
class ReplayStep:
    action: ReplayAction
    request: bytes | None
    responses: tuple[bytes, ...]


class FixtureReplayError(AssertionError):
    """The host interaction diverged from the normative fixture."""


class FixtureReplayTransport:
    """Strict ByteStream that releases fixture responses after exact host actions."""

    dtr_suppression_guaranteed = False

    def __init__(self, steps: Iterable[ReplayStep]) -> None:
        self._steps = deque(steps)
        self._incoming = bytearray()
        self.writes: list[bytes] = []
        self.resets = 0
        self.closed = False

    @property
    def complete(self) -> bool:
        return not self._steps and not self._incoming

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        del timeout
        if size < 1 or not self._incoming:
            return b""
        result = bytes(self._incoming[:size])
        del self._incoming[:size]
        return result

    def write(self, data: bytes) -> int:
        if self.closed:
            raise OSError("fixture replay transport is closed")
        self.writes.append(data)
        self._consume(ReplayAction.WRITE, data)
        return len(data)

    def reset(self) -> None:
        if self.closed:
            raise OSError("fixture replay transport is closed")
        self.resets += 1
        self._consume(ReplayAction.RESET, None)

    def close(self) -> None:
        self.closed = True

    def assert_complete(self) -> None:
        if not self.complete:
            raise FixtureReplayError(
                f"fixture replay incomplete: {len(self._steps)} actions and "
                f"{len(self._incoming)} response bytes remain"
            )

    def _consume(self, action: ReplayAction, request: bytes | None) -> None:
        if not self._steps:
            raise FixtureReplayError(f"unexpected {action}: {request!r}")
        step = self._steps.popleft()
        if step.action is not action or step.request != request:
            raise FixtureReplayError(
                f"fixture expected {step.action} {step.request!r}, got {action} {request!r}"
            )
        for response in step.responses:
            self._incoming.extend(response)


def decode_c_escaped_bytes(value: str) -> bytes:
    """Decode the small ASCII/C-escape vocabulary used by protocol fixtures."""
    output = bytearray()
    index = 0
    while index < len(value):
        character = value[index]
        if character != "\\":
            codepoint = ord(character)
            if codepoint > 0x7F:
                raise ValueError("fixture values must be ASCII")
            output.append(codepoint)
            index += 1
            continue
        if index + 1 >= len(value):
            raise ValueError("fixture value ends with an incomplete escape")
        escaped = value[index + 1]
        if escaped in {"n", "r", "t", "\\"}:
            output.append({"n": 0x0A, "r": 0x0D, "t": 0x09, "\\": 0x5C}[escaped])
            index += 2
            continue
        if escaped == "x" and index + 3 < len(value):
            digits = value[index + 2 : index + 4]
            if _HEX.fullmatch(digits):
                output.append(int(digits, 16))
                index += 4
                continue
        raise ValueError(f"unsupported fixture escape at offset {index}")
    return bytes(output)


def load_v1_wire_fixture(path: Path) -> tuple[FixtureExchange, ...]:
    """Load request/response pairs from the repository's legacy golden fixture."""
    exchanges: list[FixtureExchange] = []
    section = ""
    request: bytes | None = None
    request_marker: str | None = None
    for line_number, raw_line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        if not raw_line or raw_line.startswith("#"):
            continue
        if raw_line.startswith("[") and raw_line.endswith("]"):
            section = raw_line[1:-1]
            continue
        if raw_line.startswith("request="):
            value = raw_line.removeprefix("request=")
            request_marker = value if value.startswith("<") and value.endswith(">") else None
            request = None if request_marker is not None else decode_c_escaped_bytes(value)
            continue
        if raw_line.startswith(("precondition=", "postcondition=", "maximum-rate=")):
            continue
        if raw_line.startswith("response=") and (request is not None or request_marker is not None):
            value = raw_line.removeprefix("response=")
            response_marker = value if value.startswith("<") else None
            exchanges.append(
                FixtureExchange(
                    section,
                    request,
                    request_marker,
                    None if response_marker is not None else decode_c_escaped_bytes(value),
                    response_marker,
                )
            )
            request = None
            request_marker = None
            continue
        if raw_line.startswith("response="):
            # Unsolicited progress/fault evidence has no host action to replay.
            continue
        raise ValueError(f"invalid v1 fixture line {line_number}: {raw_line!r}")
    if request is not None or request_marker is not None:
        raise ValueError("v1 fixture ends without a response")
    return tuple(exchanges)


def load_line_fixture(path: Path) -> tuple[bytes, ...]:
    """Load escaped wire lines while ignoring comments and blank lines."""
    return tuple(
        decode_c_escaped_bytes(raw_line)
        for raw_line in path.read_text(encoding="ascii").splitlines()
        if raw_line and not raw_line.startswith("#")
    )

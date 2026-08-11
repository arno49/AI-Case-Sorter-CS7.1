"""Deterministic protocol simulator implementing the host ByteStream boundary."""

from __future__ import annotations

import logging
import random
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from cs71_protocol import CrcError, append_crc, remove_and_verify_crc

from .clock import ManualClock

SIMULATOR_EVIDENCE_CLASS = "SIMULATOR_ONLY"
_REQUEST = re.compile(r"(?:@([0-9]+) )?(.+)\Z")


class SimulatorMode(StrEnum):
    V1 = "v1"
    V2 = "v2"


class TranscriptDirection(StrEnum):
    HOST_TO_SIMULATOR = "host_to_simulator"
    SIMULATOR_TO_HOST = "simulator_to_host"
    RESET = "reset"


class AdverseScenario(StrEnum):
    DISCONNECT = "disconnect-on-sort"
    EVENT_GAP = "event-gap-on-ping"
    FAULT = "fault-feed-overtravel"
    MALFORMED_FRAME = "malformed-on-sort"
    TERMINAL_MISMATCH = "terminal-mismatch-on-sort"
    TIMEOUT = "timeout-on-sort"


_SCENARIO_TRIGGER = {
    AdverseScenario.DISCONNECT: "sortto:3",
    AdverseScenario.EVENT_GAP: "ping",
    AdverseScenario.FAULT: "sortto:3",
    AdverseScenario.MALFORMED_FRAME: "sortto:3",
    AdverseScenario.TERMINAL_MISMATCH: "sortto:3",
    AdverseScenario.TIMEOUT: "sortto:3",
}


@dataclass(frozen=True, slots=True)
class TranscriptEntry:
    at_ms: int
    direction: TranscriptDirection
    data: bytes


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    scenario: str = "happy-v2"
    seed: int = 0
    v2_available: bool = True
    crc_available: bool = True
    operation_duration_ms: int = 25
    operation_jitter_ms: int = 0
    slot_count: int = 8
    slot_max: int = 102

    def __post_init__(self) -> None:
        if not self.scenario or any(character.isspace() for character in self.scenario):
            raise ValueError("scenario must be a non-empty token")
        if self.operation_duration_ms < 1:
            raise ValueError("operation_duration_ms must be positive")
        if self.operation_jitter_ms < 0:
            raise ValueError("operation_jitter_ms must be non-negative")
        if not 1 <= self.slot_count <= 64:
            raise ValueError("slot_count must be 1..64")
        if not 1 <= self.slot_max <= 102:
            raise ValueError("slot_max must be 1..102")


class SimulatorTransport:
    """No-hardware CS7.1 byte stream with explicit time control.

    Synchronous protocol queries emit bytes during ``write``. Physical lifecycle
    terminals are scheduled and appear only after ``advance`` is called.
    """

    dtr_suppression_guaranteed = False

    def __init__(
        self,
        config: SimulatorConfig | None = None,
        *,
        clock: ManualClock | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config or SimulatorConfig()
        self.clock = clock or ManualClock()
        self._logger = logger or logging.getLogger("cs71d.simulator")
        self._random = random.Random(self.config.seed)
        self._condition = threading.Condition()
        self._incoming = bytearray()
        self._outgoing = bytearray()
        self._transcript: list[TranscriptEntry] = []
        self.closed = False
        self.resets = 0
        self._logger.warning(
            "CS71 %s backend active: %s", SIMULATOR_EVIDENCE_CLASS, self.backend_identity
        )
        self._reset_state(emit_startup=True)

    @property
    def backend_identity(self) -> str:
        return f"{SIMULATOR_EVIDENCE_CLASS}:scenario={self.config.scenario}:seed={self.config.seed}"

    @property
    def transcript(self) -> tuple[TranscriptEntry, ...]:
        return tuple(self._transcript)

    @property
    def mode(self) -> SimulatorMode:
        return self._mode

    @property
    def crc_enabled(self) -> bool:
        return self._crc_enabled

    @property
    def phase(self) -> str:
        return self._phase

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        if size < 1:
            return b""
        with self._condition:
            if self._read_error is not None:
                raise self._read_error
            if not self._outgoing and not self.closed and timeout is not None:
                self._condition.wait(timeout)
            if self._read_error is not None:
                raise self._read_error
            if not self._outgoing:
                return b""
            result = bytes(self._outgoing[:size])
            del self._outgoing[:size]
            return result

    def write(self, data: bytes) -> int:
        with self._condition:
            if self.closed:
                raise OSError("simulator transport is closed")
            self._record(TranscriptDirection.HOST_TO_SIMULATOR, data)
            self._incoming.extend(data)
            while True:
                newline = self._incoming.find(b"\n")
                if newline < 0:
                    break
                raw_line = bytes(self._incoming[:newline])
                del self._incoming[: newline + 1]
                if raw_line.endswith(b"\r"):
                    raw_line = raw_line[:-1]
                self._handle_line(raw_line)
        return len(data)

    def advance(self, delta_ms: int) -> None:
        with self._condition:
            self.clock.advance(delta_ms)

    def drain_output(self) -> bytes:
        with self._condition:
            result = bytes(self._outgoing)
            self._outgoing.clear()
            return result

    def wait_until_scheduled(self, timeout: float | None = None) -> bool:
        """Wait for test coordination without advancing simulator time."""
        with self._condition:
            return (
                self._condition.wait_for(
                    lambda: self.clock.pending_count > 0 or self.closed,
                    timeout,
                )
                and not self.closed
            )

    def reset(self) -> None:
        with self._condition:
            if self.closed:
                raise OSError("simulator transport is closed")
            self.resets += 1
            self._record(TranscriptDirection.RESET, b"")
            self._incoming.clear()
            self._outgoing.clear()
            self.clock.clear()
            self._reset_state(emit_startup=True)

    def close(self) -> None:
        with self._condition:
            self.closed = True
            self.clock.clear()
            self._condition.notify_all()

    def _reset_state(self, *, emit_startup: bool) -> None:
        self._mode = SimulatorMode.V1
        self._crc_enabled = False
        self._event_sequence = 0
        self._active_id: int | None = None
        self._machine_mode = "recovering"
        self._phase = "idle"
        self._feed_homed = False
        self._sort_homed = False
        self._queue_previous = 0
        self._queue_next = 0
        self._config_generation = 0
        self._fault_code = 0
        self._read_error: OSError | None = None
        self._boot_at_ms = self.clock.now_ms
        if emit_startup:
            self._emit_unprotected("Ready")

    def _handle_line(self, raw_line: bytes) -> None:
        if raw_line == b"stop":
            self._handle_priority_stop()
            return
        try:
            line = raw_line.decode("ascii")
        except UnicodeDecodeError:
            if self._mode is SimulatorMode.V1:
                self._emit_unprotected("error:invalid command")
            else:
                self._emit_event("reject:1001:bad_frame")
            return

        if self._mode is SimulatorMode.V1:
            self._handle_v1(line)
        else:
            self._handle_v2(line)

    def _handle_v1(self, line: str) -> None:
        if len(line.encode("ascii")) > 40:
            self._emit_unprotected("error:command too long")
        elif line == "ping":
            self._emit_unprotected(" ok")
        elif line == "protocol:2?":
            self._emit_unprotected("protocol:2 available" if self.config.v2_available else "ok")
        elif line == "protocol:2" and self.config.v2_available:
            self._emit_unprotected("protocol:2 ready")
            self._mode = SimulatorMode.V2
            self._crc_enabled = False
            self._event_sequence = 0
        elif line == "version":
            self._emit_unprotected("CS71 7.1.260714.6")
        else:
            self._emit_unprotected("ok")

    def _handle_v2(self, line: str) -> None:
        if len(line.encode("ascii")) > 64:
            self._emit_event("reject:1001:bad_frame")
            return
        try:
            body = remove_and_verify_crc(line, required=self._crc_enabled)
        except CrcError:
            self._emit_event("reject:1002:bad_crc")
            return

        match = _REQUEST.fullmatch(body)
        if match is None:
            self._emit_event("reject:1001:bad_frame")
            return
        request_id = int(match.group(1) or "0")
        command = match.group(2)
        if request_id > 65535 or (match.group(1) is not None and request_id == 0):
            self._emit_event("reject:1003:bad_id")
            return
        if command != command.strip() or any(
            not 0x20 <= ord(character) <= 0x7E for character in command
        ):
            self._emit_event("reject:1001:bad_frame")
            return
        if request_id != 0 and request_id == self._active_id:
            self._emit_event(f"reject:1003:bad_id id={request_id}")
            return
        if request_id == 0 and self._active_id == 0:
            self._respond(0, "error:2001:busy active_id=0")
            return
        if command == "stop":
            self._handle_correlated_stop(request_id)
            return
        if self._inject_adverse_scenario(request_id, command):
            return
        state_changing = command in {
            "crc:on",
            "crc:off",
            "protocol:1",
            "homefeeder",
            "homesorter",
            "homeall",
        } or command.startswith("sortto:")
        if state_changing and self._active_id is not None:
            self._respond(request_id, f"error:2001:busy active_id={self._active_id}")
            return

        if command == "protocolversion":
            self._respond_data(request_id, ("protocol=2",))
        elif command == "capabilities":
            self._respond_data(request_id, self._capability_lines())
        elif command == "status":
            self._respond_data(request_id, self._status_lines())
        elif command == "queue":
            self._respond_data(
                request_id,
                (
                    "queue_depth=2",
                    f"queue_previous={self._queue_previous}",
                    f"queue_next={self._queue_next}",
                ),
            )
        elif command == "ping":
            self._respond(request_id, f"done:uptime_ms={self.clock.now_ms - self._boot_at_ms}")
        elif command == "version":
            self._respond(request_id, "data:version=7.1.260714.6")
            self._respond(request_id, "done")
        elif command == "crc:on":
            self._enable_crc(request_id)
        elif command == "crc:off":
            self._disable_crc(request_id)
        elif command == "protocol:1":
            self._respond(request_id, "done:protocol=1")
            self._mode = SimulatorMode.V1
            self._crc_enabled = False
            self._event_sequence = 0
        elif command.startswith("sortto:"):
            self._start_sort(request_id, command[7:])
        elif command in {"homefeeder", "homesorter", "homeall"}:
            self._start_home(request_id, command)
        else:
            self._respond(request_id, "error:1004:unknown_command")

    def _inject_adverse_scenario(self, request_id: int, command: str) -> bool:
        try:
            scenario = AdverseScenario(self.config.scenario)
        except ValueError:
            return False
        if command != _SCENARIO_TRIGGER[scenario]:
            return False

        if scenario is AdverseScenario.DISCONNECT:
            self._read_error = OSError("simulated serial disconnect")
            self._condition.notify_all()
        elif scenario is AdverseScenario.EVENT_GAP:
            self._emit_event(f"state:mode={self._machine_mode} phase=idle")
            self._event_sequence = 1 if self._event_sequence == 65535 else self._event_sequence + 1
            self._emit_event(f"state:mode={self._machine_mode} phase=idle")
            self._respond(request_id, "done:uptime_ms=0")
        elif scenario is AdverseScenario.FAULT:
            self._start_injected_fault(request_id)
        elif scenario is AdverseScenario.MALFORMED_FRAME:
            self._emit_unprotected(f"@{request_id} d\x01ne")
        elif scenario is AdverseScenario.TERMINAL_MISMATCH:
            mismatched_id = 1 if request_id == 65535 else request_id + 1
            self._respond(mismatched_id, "done:slot=3")
        elif scenario is AdverseScenario.TIMEOUT:
            pass
        return True

    def _start_injected_fault(self, request_id: int) -> None:
        if not self._start_operation(request_id, operation="sort", phase="sort_move"):
            return

        def fail() -> None:
            if self._active_id != request_id:
                return
            self._active_id = None
            self._machine_mode = "recovering"
            self._phase = "idle"
            self._feed_homed = False
            self._fault_code = 3001
            self._respond(request_id, "error:3001:feed_overtravel")
            self._emit_event("fault:3001:feed_overtravel latched=1")
            self._emit_event("state:mode=recovering phase=idle")

        self._schedule(self._operation_delay_ms(), fail)

    def _enable_crc(self, request_id: int) -> None:
        if not self.config.crc_available:
            self._respond(request_id, "error:1007:unsupported key=crc")
            return
        self._crc_enabled = True
        self._respond(request_id, "done:crc=on")

    def _disable_crc(self, request_id: int) -> None:
        self._respond(request_id, "done:crc=off")
        self._crc_enabled = False

    def _start_sort(self, request_id: int, slot_text: str) -> None:
        if not slot_text.isdecimal():
            self._respond(request_id, "error:1005:invalid_argument key=slot")
            return
        slot = int(slot_text)
        if slot > self.config.slot_max:
            self._respond(
                request_id,
                f"error:1006:out_of_range key=slot min=0 max={self.config.slot_max}",
            )
            return
        if self._machine_mode == "stopped":
            self._respond(request_id, "error:2003:stopped")
            return
        if not self._sort_homed:
            self._respond(request_id, "error:2002:not_homed axis=sorter")
            return
        if not self._start_operation(request_id, operation="sort", phase="sort_move"):
            return

        def complete() -> None:
            self._queue_previous = slot
            self._queue_next = slot
            self._complete_operation(request_id, f"done:slot={slot}")

        self._schedule(self._operation_delay_ms(), complete)

    def _start_home(self, request_id: int, command: str) -> None:
        operation = "home"
        initial_phase = "feed_home" if command in {"homefeeder", "homeall"} else "sort_home"
        if not self._start_operation(request_id, operation=operation, phase=initial_phase):
            return
        delay_ms = self._operation_delay_ms()

        if command == "homeall":

            def progress_sorter() -> None:
                if self._active_id != request_id:
                    return
                self._phase = "sort_home"
                self._respond(request_id, "progress:phase=sort_home")
                self._emit_event("state:mode=recovering phase=sort_home")

            self._respond(request_id, "progress:phase=feed_home")
            self._schedule(max(1, delay_ms // 2), progress_sorter)

        def complete() -> None:
            if command in {"homefeeder", "homeall"}:
                self._feed_homed = True
            if command in {"homesorter", "homeall"}:
                self._sort_homed = True
            fields = f"feed_homed={int(self._feed_homed)} sort_homed={int(self._sort_homed)}"
            self._complete_operation(request_id, f"done:{fields}")

        self._schedule(delay_ms, complete)

    def _start_operation(self, request_id: int, *, operation: str, phase: str) -> bool:
        if self._active_id is not None:
            self._respond(
                request_id,
                f"error:2001:busy active_id={self._active_id}",
            )
            return False
        self._active_id = request_id
        self._machine_mode = (
            "running" if operation != "home" and self._both_axes_homed else "recovering"
        )
        self._phase = phase
        self._respond(request_id, f"accepted:operation={operation}")
        self._emit_event(f"state:mode={self._machine_mode} phase={phase}")
        return True

    def _complete_operation(self, request_id: int, terminal: str) -> None:
        if self._active_id != request_id:
            return
        self._active_id = None
        self._machine_mode = "running" if self._both_axes_homed else "recovering"
        self._phase = "idle"
        self._emit_event(f"state:mode={self._machine_mode} phase=idle")
        self._respond(request_id, terminal)

    @property
    def _both_axes_homed(self) -> bool:
        return self._feed_homed and self._sort_homed

    def _handle_priority_stop(self) -> None:
        self.clock.clear()
        self._active_id = None
        self._machine_mode = "stopped"
        self._phase = "idle"
        self._feed_homed = False
        self._sort_homed = False
        if self._mode is SimulatorMode.V2:
            self._emit_event("state:mode=stopped phase=idle")
        self._emit_unprotected("stopped")

    def _handle_correlated_stop(self, request_id: int) -> None:
        active_id = self._active_id
        self.clock.clear()
        if active_id is not None:
            self._respond(active_id, f"error:2004:cancelled by={request_id}")
        self._active_id = None
        self._machine_mode = "stopped"
        self._phase = "idle"
        self._feed_homed = False
        self._sort_homed = False
        self._emit_event("state:mode=stopped phase=idle")
        self._respond(request_id, "done:mode=stopped")

    def _schedule(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.clock.call_later(delay_ms, callback)
        self._condition.notify_all()

    def _operation_delay_ms(self) -> int:
        jitter = self._random.randint(0, self.config.operation_jitter_ms)
        return self.config.operation_duration_ms + jitter

    def _capability_lines(self) -> tuple[str, ...]:
        crc = "optional" if self.config.crc_available else "none"
        return (
            f"protocol=2 max_line=64 crc={crc}",
            f"queue_depth=2 slot_max={self.config.slot_max} slot_count={self.config.slot_count}",
            "pwm=0 airdrop=1 feed_sensor=1",
            "feed_home=1 sort_home=1",
        )

    def _status_lines(self) -> tuple[str, ...]:
        active_id = "none" if self._active_id is None else str(self._active_id)
        return (
            f"mode={self._machine_mode} phase={self._phase} feed_homed={int(self._feed_homed)}",
            f"sort_homed={int(self._sort_homed)} motor_enabled=0 active_id={active_id}",
            (
                f"fault_code={self._fault_code} "
                f"queue_previous={self._queue_previous} queue_next={self._queue_next}"
            ),
            f"config_generation={self._config_generation}",
        )

    def _respond_data(self, request_id: int, fields: tuple[str, ...]) -> None:
        for field_line in fields:
            self._respond(request_id, f"data:{field_line}")
        self._respond(request_id, "done")

    def _respond(self, request_id: int, body: str) -> None:
        self._emit(f"@{request_id} {body}")

    def _emit_event(self, body: str) -> None:
        self._event_sequence = 1 if self._event_sequence == 65535 else self._event_sequence + 1
        self._emit(f"!{self._event_sequence} {body}")

    def _emit(self, line: str) -> None:
        protected = append_crc(line) if self._crc_enabled else line
        if len(protected.encode("ascii")) > 64:
            raise RuntimeError(f"simulator emitted an oversized v2 frame: {protected!r}")
        self._emit_unprotected(protected)

    def _emit_unprotected(self, line: str) -> None:
        data = line.encode("ascii") + b"\n"
        with self._condition:
            self._outgoing.extend(data)
            self._record(TranscriptDirection.SIMULATOR_TO_HOST, data)
            self._condition.notify_all()

    def _record(self, direction: TranscriptDirection, data: bytes) -> None:
        self._transcript.append(TranscriptEntry(self.clock.now_ms, direction, data))

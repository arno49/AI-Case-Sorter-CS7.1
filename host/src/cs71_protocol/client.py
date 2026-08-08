"""High-level safe discovery, activation, lifecycle, and recovery API."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic

from .crc import append_crc
from .errors import ParseError, ProtocolError, RecoveryError, TimeoutError
from .framing import ByteStream, LineReader
from .models import (Capabilities, Completion, Event, Fault, QueueSnapshot, Response, ResponseKind,
                     SessionMode, Status, uint)
from .parsing import DiscoveryReply, classify_discovery, format_request, parse_v2_line
from .tracking import EventTracker, RequestTracker


class ProtocolClient:
    """A no-hardware client over an injected byte stream.

    Recovery deliberately uses only exact ID-less ``stop`` followed by a reset
    and v1 ``Ready``/``ping`` verification when mode or CRC boundaries are
    uncertain.  It never guesses the active parser.
    """

    def __init__(self, transport: ByteStream, *, timeout: float = 1.0,
                 reset: Callable[[], None] | None = None,
                 on_event: Callable[[Event, bool], None] | None = None,
                 on_recovery: Callable[[], None] | None = None) -> None:
        self.transport = transport
        self.timeout = timeout
        self.reset_hook = reset if reset is not None else getattr(transport, "reset", None)
        self.on_event = on_event
        self.on_recovery = on_recovery
        self.reader = LineReader(transport)
        self.mode = SessionMode.V1
        self.crc_enabled = False
        self.requests = RequestTracker()
        self.events = EventTracker()
        self.status: Status | None = None

    def _write_v1(self, command: str) -> None:
        if "\n" in command or "\r" in command:
            raise ParseError("commands must not contain a line terminator")
        self._write_bytes(command.encode("ascii") + b"\n")

    def _write_bytes(self, data: bytes) -> None:
        written = self.transport.write(data)
        if written is not None and written != len(data):
            raise OSError(f"short transport write: wrote {written} of {len(data)} bytes")

    def _read_v1(self, timeout: float | None = None) -> str:
        return self.reader.read_line(self.timeout if timeout is None else timeout, v2=False)

    def _read_v2(self, *, crc_required: bool | None = None, timeout: float | None = None) -> Response | Event | str:
        return parse_v2_line(self.reader.read_line(self.timeout if timeout is None else timeout, v2=True),
                             crc_required=self.crc_enabled if crc_required is None else crc_required)

    def wait_ready(self) -> None:
        """Require the exact v1 startup marker."""
        if self._read_v1() != "Ready":
            raise ParseError("expected exact v1 Ready")

    def v1_ping_barrier(self) -> None:
        """Run the exact legacy idle barrier (including its leading space)."""
        try:
            self._write_v1("ping")
            if self._read_v1() != " ok":
                raise ParseError("expected exact legacy ping response ' ok'")
        except Exception as exc:
            self._fail_request("v1 ping barrier was unsafe", exc)

    def discover(self) -> bool:
        """Return whether v2 is available; a legacy ``ok`` is conclusively false."""
        try:
            for attempt in range(2):
                self._write_v1("protocol:2?")
                line = self._read_v1()
                if line == "error:busy" and attempt == 0:
                    self.v1_ping_barrier()
                    continue
                return classify_discovery(line) is DiscoveryReply.AVAILABLE
            raise AssertionError("unreachable")
        except RecoveryError:
            raise
        except Exception as exc:
            self._fail_request("discovery was unsafe", exc)

    def activate(self) -> bool:
        """Discover and enter v2, recovering to verified v1 on any uncertainty."""
        try:
            if not self.discover():
                return False
            self._write_v1("protocol:2")
            if self._read_v1() != "protocol:2 ready":
                raise ParseError("activation requires exact 'protocol:2 ready'")
            self.mode = SessionMode.V2
            self.crc_enabled = False
            self.requests.clear()
            self.events = EventTracker()
            self.get_protocol_version()
            self.get_capabilities()
            self.get_status()
            return True
        except RecoveryError:
            raise
        except Exception as exc:
            try:
                self.recover_to_v1()
            except RecoveryError as recovery_error:
                raise RecoveryError("activation was uncertain; v1 recovery failed") from recovery_error
            raise RecoveryError("activation was uncertain; recovered to v1") from exc

    def recover_to_v1(self) -> None:
        """Use universal stop/reset and verify v1 before allowing further work."""
        self.requests.clear()
        self.mode = SessionMode.UNCERTAIN
        self.crc_enabled = False
        try:
            self._write_v1("stop")
            # Stop may be emitted among v2 events, but the literal terminal must arrive.
            deadline = monotonic() + self.timeout
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for exact stop terminal")
                line = self.reader.read_line(remaining, v2=False)
                if line == "stopped":
                    break
        except Exception:
            # A reset is authoritative even if an uncertain parser could not answer stop.
            pass
        if not callable(self.reset_hook):
            raise RecoveryError("session is uncertain and no reset hook is available")
        try:
            self.reset_hook()
        except Exception as exc:
            raise RecoveryError("reset failed; could not verify a v1 session") from exc
        self.reader.clear()
        verify_deadline = monotonic() + self.timeout
        try:
            remaining = verify_deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for exact v1 Ready")
            if self._read_v1(remaining) != "Ready":
                raise ParseError("expected exact v1 Ready")
            self._write_v1("ping")
            remaining = verify_deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for exact legacy ping response")
            if self._read_v1(remaining) != " ok":
                raise ParseError("expected exact legacy ping response ' ok'")
        except Exception as exc:
            raise RecoveryError("reset did not verify a v1 session") from exc
        self.mode = SessionMode.V1
        self.crc_enabled = False
        self.requests.clear()
        self.events = EventTracker()
        self.status = None
        if self.on_recovery:
            self.on_recovery()

    def _observe_event(self, event: Event) -> None:
        contiguous = self.events.observe(event)
        if self.on_event:
            self.on_event(event, contiguous)

    def _start(self, command: str, request_id: int | None) -> int:
        if self.mode is not SessionMode.V2:
            raise ProtocolError("v2 request attempted outside an active v2 session")
        if request_id is None:
            request_id = self.requests.allocate()
        else:
            self.requests.reserve(request_id)
        try:
            frame = format_request(request_id, command, crc=self.crc_enabled)
        except ProtocolError:
            self.requests.release(request_id)
            raise
        try:
            self._write_bytes((frame + "\n").encode("ascii"))
        except Exception as exc:
            self._fail_request("request transmission failed", exc)
        return request_id

    def _fail_request(self, message: str, cause: Exception) -> None:
        """Fail closed after a transmitted request cannot be safely correlated."""
        self.requests.clear()
        try:
            self.recover_to_v1()
        except RecoveryError as exc:
            raise RecoveryError(f"{message}; v1 recovery failed") from exc
        raise RecoveryError(f"{message}; recovered to v1") from cause

    def request(self, command: str, *, request_id: int | None = None,
                timeout: float | None = None, response_crc: bool | None = None) -> Completion:
        """Send one v2 request and return its correlated terminal completion."""
        request_id = self._start(command, request_id)
        responses: list[Response] = []
        deadline = monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            if deadline - monotonic() <= 0:
                self._fail_request("request timed out", TimeoutError("request deadline expired"))
            try:
                item = self._read_v2(crc_required=response_crc, timeout=deadline - monotonic())
            except Exception as exc:
                self._fail_request("request timed out" if isinstance(exc, TimeoutError)
                                   else "request response was unsafe", exc)
            if isinstance(item, Event):
                self._observe_event(item)
                continue
            try:
                if item == "stopped":
                    raise ProtocolError("out-of-band stop cancelled request")
                self.requests.observe(item)
                if item.request_id != request_id:
                    # It was valid for another outstanding caller, but this synchronous API
                    # cannot consume it without risking its completion.
                    raise ProtocolError(f"interleaved response for active ID {item.request_id}")
            except ProtocolError as exc:
                self._fail_request("request response was unsafe", exc)
            responses.append(item)
            if item.terminal:
                fields: dict[str, str] = {}
                for response in responses:
                    fields.update(response.fields)
                return Completion(request_id, item.kind is ResponseKind.DONE, fields, tuple(responses),
                                  None if item.kind is ResponseKind.DONE else Fault.from_response(item))

    def enable_crc(self, *, request_id: int | None = None) -> Completion:
        """Enable CRC; the terminal enable response is the first protected frame."""
        if self.crc_enabled:
            raise ProtocolError("CRC is already enabled")
        completion = self.request("crc:on", request_id=request_id, response_crc=True)
        if not completion.succeeded or completion.terminal_fields.get("crc") != "on":
            self._fail_request("CRC enable boundary was not confirmed",
                               ProtocolError("terminal crc=on was required"))
        self.crc_enabled = True
        return completion

    def disable_crc(self, *, request_id: int | None = None) -> Completion:
        """Disable CRC after receiving the final protected terminal response."""
        if not self.crc_enabled:
            raise ProtocolError("CRC is already disabled")
        completion = self.request("crc:off", request_id=request_id)
        if not completion.succeeded or completion.terminal_fields.get("crc") != "off":
            self._fail_request("CRC disable boundary was not confirmed",
                               ProtocolError("terminal crc=off was required"))
        self.crc_enabled = False
        return completion

    def leave_v2(self, *, request_id: int | None = None) -> Completion:
        """Return through the healthy correlated protocol:1 boundary."""
        try:
            completion = self.request("protocol:1", request_id=request_id)
            if not completion.succeeded or completion.terminal_fields.get("protocol") != "1":
                raise ProtocolError("protocol:1 was not confirmed")
        except RecoveryError:
            raise
        except ProtocolError as exc:
            self.recover_to_v1()
            raise RecoveryError("v2 leave was uncertain; recovered to v1") from exc
        self.mode = SessionMode.V1
        self.crc_enabled = False
        self.requests.clear()
        self.events = EventTracker()
        return completion

    def get_status(self, *, request_id: int | None = None) -> Status:
        completion = self.request("status", request_id=request_id)
        if not completion.succeeded:
            raise ProtocolError(f"status failed: {completion.error}")
        self.status = Status.from_fields(completion.fields)
        self.events.replace_status()
        return self.status

    def get_protocol_version(self, *, request_id: int | None = None) -> int:
        completion = self.request("protocolversion", request_id=request_id)
        if not completion.succeeded:
            raise ProtocolError(f"protocolversion failed: {completion.error}")
        if "protocol" not in completion.fields:
            raise ParseError("protocolversion missing required protocol")
        version = uint(completion.fields["protocol"], "protocol")
        if version != 2:
            raise ParseError("unsupported protocol version")
        return version

    def get_capabilities(self, *, request_id: int | None = None) -> Capabilities:
        completion = self.request("capabilities", request_id=request_id)
        if not completion.succeeded:
            raise ProtocolError(f"capabilities failed: {completion.error}")
        return Capabilities.from_fields(completion.fields)

    def get_queue(self, *, request_id: int | None = None) -> QueueSnapshot:
        completion = self.request("queue", request_id=request_id)
        if not completion.succeeded:
            raise ProtocolError(f"queue failed: {completion.error}")
        return QueueSnapshot.from_fields(completion.fields)

    def out_of_band_stop(self) -> None:
        """Send the universal CRC-exempt recovery command and require ``stopped``."""
        try:
            self._write_v1("stop")
            # The reader is deliberately raw/v1 here: v2 may emit a state event before
            # the sole unframed terminal, and CRC state is intentionally irrelevant.
            deadline = monotonic() + self.timeout
            while True:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for exact stop terminal")
                if self._read_v1(remaining) == "stopped":
                    return
        except Exception as exc:
            self._fail_request("out-of-band stop was unsafe", exc)

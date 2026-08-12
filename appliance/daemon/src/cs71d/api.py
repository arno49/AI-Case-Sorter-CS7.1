"""The private `cs71d` HTTP/JSON API, served on a Unix domain socket only.

This module has no TCP code path at all. The server binds ``AF_UNIX``, creates
its socket with owner/group-only permissions, and a static test asserts that no
daemon module ever names an internet address family. A browser cannot reach it;
only the local SvelteKit service can, and only with the installation-local
bearer credential the contract requires.

The executable contract in ``appliance/contracts/cs71d-v1.openapi.json`` is the
source of truth for this surface. Responses here are shaped to it, and the
daemon's internal vocabulary is translated at this boundary rather than leaking
outward: lower-case internal enums become the contract's upper-case ones, and
protocol internals never appear in an error body.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import socket
import socketserver
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from .adapters import FEED_LIFECYCLE_GATE
from .domain import OperationDomain
from .journal import Journal, JournalError
from .machine import FaultState, MachineSnapshot
from .operations import Actor, DomainError, OperationAction, OperationRecord, OperationState

API_VERSION = "v1"
MAX_BODY_BYTES = 64 * 1024
MAX_CONTRACT_DEADLINE_MS = 120_000
MAX_API_SLOT = 63
COMMANDING_ROLES = frozenset({"operator", "administrator"})
API_ROLES = frozenset({"viewer", *COMMANDING_ROLES})
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
SOCKET_MODE = 0o660
SHUTDOWN_POLL_SECONDS = 0.02

_LOGGER = logging.getLogger("cs71d.api")
_REQUEST_ID = re.compile(r"[0-9a-fA-F-]{36}\Z")
_OPERATION_PATH = re.compile(r"/v1/operations/(?P<operation_id>[0-9a-fA-F-]{36})\Z")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._~-]{16,128}\Z")
_EXACT_GENERATION = re.compile(r"(0|[1-9][0-9]*)\Z")

_SESSION_PATHS = frozenset({"/v1/session/connect", "/v1/session/recover"})

_COMMAND_ACTIONS: Mapping[str, OperationAction] = {
    "/v1/operations/home": OperationAction.HOME,
    "/v1/operations/sort": OperationAction.SORT,
    "/v1/operations/feed": OperationAction.FEED,
}

# The contract's HTTP mapping. Keeping it here, keyed by the domain error code,
# means a new domain error cannot silently acquire a default status.
_STATUS_FOR_CODE: Mapping[str, HTTPStatus] = {
    "VALIDATION_FAILED": HTTPStatus.BAD_REQUEST,
    "UNAUTHENTICATED": HTTPStatus.UNAUTHORIZED,
    "FORBIDDEN": HTTPStatus.FORBIDDEN,
    "RESOURCE_NOT_FOUND": HTTPStatus.NOT_FOUND,
    "STALE_GENERATION": HTTPStatus.CONFLICT,
    "IDEMPOTENCY_CONFLICT": HTTPStatus.CONFLICT,
    "NOT_READY": HTTPStatus.CONFLICT,
    "UNSUPPORTED": HTTPStatus.CONFLICT,
    "UNCERTAIN": HTTPStatus.CONFLICT,
    "QUEUE_FULL": HTTPStatus.TOO_MANY_REQUESTS,
    "DEADLINE_INVALID": HTTPStatus.BAD_REQUEST,
    "DEADLINE_EXPIRED": HTTPStatus.REQUEST_TIMEOUT,
    "JOURNAL_UNAVAILABLE": HTTPStatus.SERVICE_UNAVAILABLE,
    "SERVICE_UNAVAILABLE": HTTPStatus.SERVICE_UNAVAILABLE,
    "INTERNAL_ERROR": HTTPStatus.INTERNAL_SERVER_ERROR,
}

# `UNSUPPORTED` is a daemon-internal distinction; the contract's error
# vocabulary expresses an unavailable operation as a precondition failure.
_CONTRACT_CODE: Mapping[str, str] = {"UNSUPPORTED": "NOT_READY"}

_OUTCOME_FOR_STATE: Mapping[OperationState, str] = {
    OperationState.SUCCEEDED: "COMPLETED",
    OperationState.FAILED: "FAILED",
    OperationState.CANCELLED: "CANCELLED",
    OperationState.UNCERTAIN: "UNCERTAIN",
}


class ApiError(Exception):
    """An HTTP-shaped rejection carrying a contract error code."""

    def __init__(
        self, code: str, message: str, *, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


class ApiServer:
    """Serve the private daemon API on one Unix domain socket."""

    def __init__(
        self,
        domain: OperationDomain,
        journal: Journal,
        *,
        socket_path: str | Path,
        service_token: str,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        socket_mode: int = SOCKET_MODE,
    ) -> None:
        if not service_token:
            raise ValueError("a service token is required; the API is never unauthenticated")
        self._domain = domain
        self._journal = journal
        self._socket_path = str(socket_path)
        self._token = service_token.encode("utf-8")
        self._now = now
        self._socket_mode = socket_mode
        self._server: _UnixHttpServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def socket_path(self) -> str:
        return self._socket_path

    def start(self) -> None:
        """Bind the socket and serve requests on a background thread."""
        if self._server is not None:
            return
        self._claim_socket_path()
        server = _UnixHttpServer(self._socket_path, _Handler)
        server.api = self
        os.chmod(self._socket_path, self._socket_mode)
        self._server = server
        # A short poll interval keeps shutdown prompt; it is not a busy loop.
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=SHUTDOWN_POLL_SECONDS),
            name="cs71d-api",
            daemon=True,
        )
        self._thread.start()

    def close(self, *, timeout: float = 5.0) -> None:
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout)
        self._unlink_stale_socket()

    def __enter__(self) -> ApiServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def authenticate(self, header: str | None) -> None:
        """Require the installation-local bearer credential on every request."""
        scheme, _, presented = (header or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip().encode("utf-8"), self._token
        ):
            raise ApiError("UNAUTHENTICATED", "a valid local service credential is required")

    def route(
        self,
        method: str,
        path: str,
        query: Mapping[str, list[str]],
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> _Response:
        if method == "POST":
            return self._command(path, headers or {}, body)
        if method == "PATCH" and path == "/v1/configuration":
            return self._configure(headers or {}, body)
        if method not in {"GET", "HEAD"}:
            raise ApiError("RESOURCE_NOT_FOUND", f"{method} {path} is not available")
        if path == "/v1/health/live":
            return _Response(HTTPStatus.OK, self._liveness())
        view = self._domain.snapshot
        if path == "/v1/health/ready":
            return _Response(HTTPStatus.OK, self._readiness(view))
        if path == "/v1/snapshot":
            return _Response(
                HTTPStatus.OK,
                self._machine_snapshot(view),
                etag=f'"generation:{view.generation}"',
            )
        if path == "/v1/configuration":
            return _Response(HTTPStatus.OK, self._configuration())
        if path == "/v1/operations":
            return _Response(HTTPStatus.OK, self._operation_page(query))
        matched = _OPERATION_PATH.fullmatch(path)
        if matched is not None:
            return _Response(HTTPStatus.OK, self._operation(matched.group("operation_id")))
        raise ApiError("RESOURCE_NOT_FOUND", f"{path} is not a resource of this API")

    def _command(self, path: str, headers: Mapping[str, str], body: bytes) -> _Response:
        """Admit one state-changing request from the BFF.

        Every command carries all three required headers. They are evaluated
        here so a malformed request is refused at the boundary, and the domain
        still re-evaluates the ones that guard the machine.
        """
        action = _COMMAND_ACTIONS.get(path)
        if action is None and path not in _SESSION_PATHS and path != "/v1/machine/stop":
            raise ApiError("RESOURCE_NOT_FOUND", f"POST {path} is not a resource of this API")
        payload = _json_object(body)
        actor = _commanding_actor(payload)
        key = _idempotency_key(headers)
        deadline_ms = _deadline_ms(headers)

        if path in _SESSION_PATHS:
            return _Response(
                HTTPStatus.ACCEPTED,
                _accepted_body(
                    self._session_command(
                        path,
                        payload,
                        actor=actor,
                        key=key,
                        generation=_exact_generation(headers),
                        deadline_ms=deadline_ms,
                    )
                ),
            )
        if action is None:
            _require_exact_fields(payload, {"api_version", "actor"}, "stop")
            record = self._domain.stop(
                actor=actor,
                idempotency_key=key,
                expected_generation=_stop_generation(headers),
                deadline_ms=deadline_ms,
            )
        else:
            record = self._domain.submit(
                action,
                _command_body(action, payload),
                actor=actor,
                idempotency_key=key,
                expected_generation=_exact_generation(headers),
                deadline_ms=deadline_ms,
            )
        return _Response(HTTPStatus.ACCEPTED, _accepted_body(record))

    def _session_command(
        self,
        path: str,
        payload: Mapping[str, Any],
        *,
        actor: Actor,
        key: str,
        generation: int,
        deadline_ms: int,
    ) -> OperationRecord:
        if path == "/v1/session/connect":
            _require_exact_fields(payload, {"api_version", "actor"}, "connect")
            return self._domain.connect(
                actor=actor,
                idempotency_key=key,
                expected_generation=generation,
                deadline_ms=deadline_ms,
            )
        _require_exact_fields(
            payload, {"api_version", "actor", "confirm_uncertain_recovery"}, "recover"
        )
        if payload["confirm_uncertain_recovery"] is not True:
            # Recovery tears down a session the daemon may not understand, so
            # it is never implied by the request having been sent.
            raise ApiError(
                "VALIDATION_FAILED", "recovery requires confirm_uncertain_recovery to be true"
            )
        return self._domain.recover(
            actor=actor,
            idempotency_key=key,
            expected_generation=generation,
            deadline_ms=deadline_ms,
        )

    def _configuration(self) -> dict[str, Any]:
        values, generation, updated_at = self._read(self._domain.configuration)
        return {
            "api_version": API_VERSION,
            "generation": generation,
            "values": values.as_mapping(),
            "updated_at": _rfc3339(updated_at),
        }

    def _configure(self, headers: Mapping[str, str], body: bytes) -> _Response:
        payload = _json_object(body)
        actor = _commanding_actor(payload)
        _require_exact_fields(payload, {"api_version", "actor", "changes"}, "configuration")
        changes = payload["changes"]
        if not isinstance(changes, dict) or not changes:
            raise ApiError("VALIDATION_FAILED", "changes must be a non-empty object")
        record = self._domain.configure(
            {name: _configuration_value(name, value) for name, value in changes.items()},
            actor=actor,
            idempotency_key=_idempotency_key(headers),
            expected_generation=_exact_generation(headers),
            deadline_ms=_deadline_ms(headers),
        )
        return _Response(HTTPStatus.ACCEPTED, _accepted_body(record))

    def _liveness(self) -> dict[str, Any]:
        # Liveness is process liveness only. It deliberately says nothing about
        # the controller, the session, or whether anything may move.
        return {"api_version": API_VERSION, "live": True, "observed_at": self._timestamp()}

    def _readiness(self, view: MachineSnapshot) -> dict[str, Any]:
        return {
            "api_version": API_VERSION,
            "ready": view.admits_work,
            "reason": None if view.admits_work else view.reason[:256],
            "connection_state": view.connection.name,
            "fault_state": view.fault.name,
            "generation": view.generation,
            "observed_at": self._timestamp(),
        }

    def _machine_snapshot(self, view: MachineSnapshot) -> dict[str, Any]:
        firmware = view.firmware
        readiness = view.readiness
        return {
            "api_version": API_VERSION,
            "generation": view.generation,
            "connection_state": view.connection.name,
            "fault_state": view.fault.name,
            "ready": view.admits_work,
            "readiness_reason": None if view.admits_work else view.reason[:256],
            "active_operation": self._active_operation(view),
            "firmware": {
                # Nothing is claimed before the controller has been observed:
                # an unobserved session advertises no v2 and no capability.
                "protocol_version": 2 if firmware is not None else 1,
                "capabilities": {
                    "v2_available": firmware is not None,
                    "crc_active": False,
                    "slot_count": 1 if firmware is None else min(firmware.slot_count, 64),
                    "home_available": firmware is not None
                    and (firmware.feed_home or firmware.sort_home),
                    "sort_available": readiness is not None and readiness.sort_homed,
                    "feed_available": False,
                    "feed_unavailable_reason": (
                        f"the v2 feed lifecycle gate is {FEED_LIFECYCLE_GATE}"
                    ),
                },
            },
            "machine": {
                "feed_homed": readiness is not None and readiness.feed_homed,
                "sort_homed": readiness is not None and readiness.sort_homed,
                "sorter_slot": None,
            },
            "faults": self._faults(view),
            "observed_at": self._timestamp(),
        }

    def _faults(self, view: MachineSnapshot) -> list[dict[str, Any]]:
        if view.fault is FaultState.CLEAR or view.fault_id is None:
            return []
        opened_at = view.fault_opened_at or self._now()
        return [
            {
                "fault_id": view.fault_id,
                "state": view.fault.name,
                "code": "JOURNAL_UNAVAILABLE",
                "source": "journal",
                "opened_at": _rfc3339(opened_at),
                "message": view.reason[:512],
            }
        ]

    def _active_operation(self, view: MachineSnapshot) -> dict[str, Any] | None:
        if view.active_operation_id is None:
            return None
        record = self._read(lambda: self._domain.operation(view.active_operation_id or ""))
        return None if record is None else _operation_body(record)

    def _operation(self, operation_id: str) -> dict[str, Any]:
        record = self._read(lambda: self._domain.operation(operation_id))
        if record is None:
            raise ApiError("RESOURCE_NOT_FOUND", f"operation {operation_id} is not retained")
        return _operation_body(record)

    def _operation_page(self, query: Mapping[str, list[str]]) -> dict[str, Any]:
        limit = _bounded_int(query, "limit", default=DEFAULT_PAGE_SIZE, maximum=MAX_PAGE_SIZE)
        cursor = _optional_cursor(query)
        state = _optional_enum(query, "state", OperationState)
        action = _optional_enum(query, "type", OperationAction)
        rows = self._read(
            lambda: self._journal.recent_operations(
                limit=limit, before=cursor, state=state, action=action
            )
        )
        return {
            "api_version": API_VERSION,
            "items": [_operation_body(record) for _cursor, record in rows],
            "next_cursor": str(rows[-1][0]) if len(rows) == limit else None,
        }

    def _read[T](self, query: Callable[[], T]) -> T:
        try:
            return query()
        except JournalError as exc:
            raise ApiError(
                "JOURNAL_UNAVAILABLE", f"the operation journal is unavailable: {exc}"
            ) from exc

    def _timestamp(self) -> str:
        return _rfc3339(self._now())

    def _claim_socket_path(self) -> None:
        """Take the socket path only when no daemon is already serving it.

        Unlinking unconditionally would let a second instance silently steal
        the path from a running daemon that still owns the serial port. A
        refused connection means the file is stale and safe to replace.
        """
        if not Path(self._socket_path).exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.5)
        try:
            probe.connect(self._socket_path)
        except OSError:
            os.unlink(self._socket_path)
            return
        finally:
            probe.close()
        raise OSError(f"another daemon is already serving {self._socket_path}")

    def _unlink_stale_socket(self) -> None:
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            return


class _Response:
    __slots__ = ("status", "body", "etag")

    def __init__(self, status: HTTPStatus, body: Mapping[str, Any], *, etag: str | None = None):
        self.status = status
        self.body = body
        self.etag = etag


class _UnixHttpServer(socketserver.ThreadingUnixStreamServer):
    """A threading HTTP server whose address family is Unix and only Unix."""

    daemon_threads = True
    allow_reuse_address = False
    api: ApiServer

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        connection, _address = super().get_request()
        # BaseHTTPRequestHandler expects an addressable peer; a Unix peer has
        # none, so give it a stable placeholder rather than an IP-shaped lie.
        return connection, ("unix", 0)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "cs71d"
    sys_version = ""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def address_string(self) -> str:
        return "unix"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        _LOGGER.debug("cs71d api %s", format % args)

    def _dispatch(self, method: str) -> None:
        api = self._api()
        request_id = self._request_id()
        try:
            api.authenticate(self.headers.get("Authorization"))
            body = self._read_body()
            split = urlsplit(self.path)
            response = api.route(
                method,
                split.path,
                parse_qs(split.query),
                headers={name.lower(): value for name, value in self.headers.items()},
                body=body,
            )
        except ApiError as exc:
            self._write(_error_response(exc, request_id), request_id)
        except DomainError as exc:
            self._write(_error_response(_from_domain(exc), request_id), request_id)
        except Exception:
            _LOGGER.exception("unhandled cs71d api failure")
            # Never leak an internal message or protocol detail to a caller.
            self._write(
                _error_response(
                    ApiError("INTERNAL_ERROR", "the daemon could not complete this request"),
                    request_id,
                ),
                request_id,
            )
        else:
            self._write(response, request_id)

    def _api(self) -> ApiServer:
        server = self.server
        assert isinstance(server, _UnixHttpServer)
        return server.api

    def _request_id(self) -> str:
        supplied = (self.headers.get("X-Request-ID") or "").strip()
        return supplied if _REQUEST_ID.fullmatch(supplied) else str(uuid4())

    def _read_body(self) -> bytes:
        declared = self.headers.get("Content-Length")
        if declared is None:
            return b""
        try:
            length = int(declared)
        except ValueError as exc:
            raise ApiError("VALIDATION_FAILED", "Content-Length must be an integer") from exc
        if length > MAX_BODY_BYTES:
            # Refused without reading: an oversized body is never buffered.
            raise ApiError("VALIDATION_FAILED", "the request body exceeds the daemon limit")
        return self.rfile.read(length) if length else b""

    def _write(self, response: _Response, request_id: str) -> None:
        payload = json.dumps(response.body, separators=(",", ":")).encode("utf-8")
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Request-ID", request_id)
        self.send_header("Cache-Control", "no-store")
        if response.etag is not None:
            self.send_header("ETag", response.etag)
        self.end_headers()
        self.wfile.write(payload)


def _error_response(error: ApiError, request_id: str) -> _Response:
    code = _CONTRACT_CODE.get(error.code, error.code)
    body: dict[str, Any] = {
        "api_version": API_VERSION,
        "code": code,
        "message": error.message[:512],
        "request_id": request_id,
    }
    if error.details:
        body["details"] = dict(error.details)
    return _Response(_STATUS_FOR_CODE.get(error.code, HTTPStatus.INTERNAL_SERVER_ERROR), body)


def _from_domain(error: DomainError) -> ApiError:
    details = {"operation_id": error.operation_id} if error.operation_id else None
    return ApiError(error.code, str(error), details=details)


def _operation_body(record: OperationRecord) -> dict[str, Any]:
    body: dict[str, Any] = {
        "api_version": API_VERSION,
        "operation_id": record.operation_id,
        "type": record.action.name,
        "state": record.state.name,
        "actor": {"user_id": record.actor.user_id, "role": record.actor.role},
        "created_at": _rfc3339(record.created_at),
        "deadline_at": _rfc3339(record.deadline_at),
        "generation": record.generation,
        "trusted_terminal": record.trusted_terminal,
    }
    if record.terminal_at is not None:
        body["terminal_at"] = _rfc3339(record.terminal_at)
    outcome = _OUTCOME_FOR_STATE.get(record.state)
    if outcome is not None:
        # A stop that reached its trusted terminal stopped the machine; every
        # other trusted terminal completed the work it was asked to do.
        if record.state is OperationState.SUCCEEDED and record.action is OperationAction.STOP:
            outcome = "STOPPED"
        body["outcome"] = outcome
    return body


def _accepted_body(record: OperationRecord) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "operation_id": record.operation_id,
        "state": record.state.name,
        "generation": record.generation,
        "accepted_at": _rfc3339(record.created_at),
        "status_url": f"/v1/operations/{record.operation_id}",
    }


def _json_object(body: bytes) -> dict[str, Any]:
    if not body:
        raise ApiError("VALIDATION_FAILED", "a JSON request body is required")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", "the request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError("VALIDATION_FAILED", "the request body must be a JSON object")
    if payload.get("api_version") != API_VERSION:
        raise ApiError("VALIDATION_FAILED", f"api_version must be {API_VERSION!r}")
    return payload


def _commanding_actor(payload: Mapping[str, Any]) -> Actor:
    """Read the propagated attribution and refuse a role that cannot command.

    SvelteKit authorizes the browser identity; this is not that authority. The
    daemon validates the restricted format it accepts and refuses a role that
    the contract says can only read, so a mistake at the BFF is not silently
    executed as motion.
    """
    supplied = payload.get("actor")
    if not isinstance(supplied, dict) or set(supplied) != {"user_id", "role"}:
        raise ApiError("VALIDATION_FAILED", "actor must carry exactly a user_id and a role")
    role = supplied["role"]
    if role not in API_ROLES:
        raise ApiError("VALIDATION_FAILED", "actor role is not a role of this API")
    if role not in COMMANDING_ROLES:
        raise ApiError("FORBIDDEN", f"the {role} role may not command the machine")
    try:
        return Actor(user_id=str(supplied["user_id"]), role=str(role))
    except DomainError as exc:
        raise ApiError("VALIDATION_FAILED", str(exc)) from exc


def _command_body(action: OperationAction, payload: Mapping[str, Any]) -> dict[str, Any]:
    if action is OperationAction.HOME:
        _require_exact_fields(payload, {"api_version", "actor", "target"}, "home")
        target = payload["target"]
        if not isinstance(target, str):
            raise ApiError("VALIDATION_FAILED", "home target must be a string")
        return {"axis": target}
    if action is OperationAction.SORT:
        _require_exact_fields(payload, {"api_version", "actor", "slot"}, "sort")
        return {"slot": _api_slot(payload["slot"])}
    _require_exact_fields(payload, {"api_version", "actor"}, "feed")
    # Feed carries no slot at this version; the domain refuses it at the gate.
    return {"slot": 0}


def _configuration_value(name: str, value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError("VALIDATION_FAILED", f"{name} must be an integer")
    return value


def _api_slot(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError("VALIDATION_FAILED", "slot must be an integer")
    if not 0 <= value <= MAX_API_SLOT:
        raise ApiError("VALIDATION_FAILED", f"slot must be between 0 and {MAX_API_SLOT}")
    return value


def _require_exact_fields(payload: Mapping[str, Any], allowed: set[str], name: str) -> None:
    if set(payload) != allowed:
        raise ApiError("VALIDATION_FAILED", f"a {name} request carries exactly {sorted(allowed)}")


def _idempotency_key(headers: Mapping[str, str]) -> str:
    key = headers.get("idempotency-key", "")
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ApiError("VALIDATION_FAILED", "Idempotency-Key is missing or malformed")
    return key


def _exact_generation(headers: Mapping[str, str]) -> int:
    supplied = headers.get("if-match-generation", "")
    if not _EXACT_GENERATION.fullmatch(supplied):
        raise ApiError(
            "VALIDATION_FAILED", "If-Match-Generation must be the exact observed generation"
        )
    return int(supplied)


def _stop_generation(headers: Mapping[str, str]) -> int | None:
    """A priority stop may be requested against a stale or uncertain view."""
    supplied = headers.get("if-match-generation", "")
    if supplied == "*":
        return None
    return _exact_generation(headers)


def _deadline_ms(headers: Mapping[str, str]) -> int:
    supplied = headers.get("x-deadline-ms", "")
    try:
        deadline = int(supplied)
    except ValueError as exc:
        raise ApiError("DEADLINE_INVALID", "X-Deadline-Ms is missing or not an integer") from exc
    if not 1 <= deadline <= MAX_CONTRACT_DEADLINE_MS:
        raise ApiError(
            "DEADLINE_INVALID",
            f"X-Deadline-Ms must be between 1 and {MAX_CONTRACT_DEADLINE_MS}",
        )
    return deadline


def _bounded_int(
    query: Mapping[str, list[str]],
    name: str,
    *,
    default: int,
    maximum: int,
) -> int:
    values = query.get(name)
    if not values:
        return default
    try:
        value = int(values[-1])
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise ApiError("VALIDATION_FAILED", f"{name} must be between 1 and {maximum}")
    return value


def _optional_cursor(query: Mapping[str, list[str]]) -> int | None:
    values = query.get("cursor")
    if not values:
        return None
    try:
        return int(values[-1])
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", "cursor is not a cursor issued by this daemon") from exc


def _optional_enum[T: OperationState | OperationAction](
    query: Mapping[str, list[str]],
    name: str,
    vocabulary: type[T],
) -> T | None:
    values = query.get(name)
    if not values:
        return None
    try:
        return vocabulary(values[-1].lower())
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", f"{name} is not a known {name} value") from exc


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")

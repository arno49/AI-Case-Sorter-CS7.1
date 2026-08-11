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
from .operations import DomainError, OperationAction, OperationRecord, OperationState

API_VERSION = "v1"
MAX_BODY_BYTES = 64 * 1024
DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 100
SOCKET_MODE = 0o660
SHUTDOWN_POLL_SECONDS = 0.02

_LOGGER = logging.getLogger("cs71d.api")
_REQUEST_ID = re.compile(r"[0-9a-fA-F-]{36}\Z")
_OPERATION_PATH = re.compile(r"/v1/operations/(?P<operation_id>[0-9a-fA-F-]{36})\Z")

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
        self._unlink_stale_socket()
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

    def route(self, method: str, path: str, query: Mapping[str, list[str]]) -> _Response:
        if method not in {"GET", "HEAD"}:
            # State-changing resources arrive with the command endpoints; until
            # then an honest 405 beats a route that silently accepts nothing.
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
        if path == "/v1/operations":
            return _Response(HTTPStatus.OK, self._operation_page(query))
        matched = _OPERATION_PATH.fullmatch(path)
        if matched is not None:
            return _Response(HTTPStatus.OK, self._operation(matched.group("operation_id")))
        raise ApiError("RESOURCE_NOT_FOUND", f"{path} is not a resource of this API")

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
            self._reject_unread_body()
            split = urlsplit(self.path)
            response = api.route(method, split.path, parse_qs(split.query))
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

    def _reject_unread_body(self) -> None:
        declared = self.headers.get("Content-Length")
        if declared is None:
            return
        try:
            length = int(declared)
        except ValueError as exc:
            raise ApiError("VALIDATION_FAILED", "Content-Length must be an integer") from exc
        if length > MAX_BODY_BYTES:
            raise ApiError("VALIDATION_FAILED", "the request body exceeds the daemon limit")
        if length:
            self.rfile.read(length)

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

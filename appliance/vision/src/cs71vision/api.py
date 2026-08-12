"""cs71-vision's own read-only HTTP/JSON API, served on a Unix domain socket.

Mirrors `cs71d.api`'s shape deliberately, at a fraction of its surface: one
resource (per-class dataset counts against the training floor), read-only,
authenticated with the same shared bearer credential every service in this
installation already carries (`cs71vision.runtime.read_service_token`).
`cs71-web` is the only intended caller, the same way it already is for
`cs71d`'s socket - see `docs/architecture/api-and-events.md` for the shared
rules (Unix-socket only, bearer credential, never browser-addressable).

This module has no TCP code path at all, the same guarantee
`appliance/daemon/src/cs71d/api.py` makes and tests statically.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import socket
import socketserver
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .dataset import DatasetStore

_LOGGER = logging.getLogger("cs71vision.api")

SOCKET_MODE = 0o660
SHUTDOWN_POLL_SECONDS = 0.2

_ERROR_STATUS: dict[str, HTTPStatus] = {
    "UNAUTHENTICATED": HTTPStatus.UNAUTHORIZED,
    "RESOURCE_NOT_FOUND": HTTPStatus.NOT_FOUND,
    "INTERNAL_ERROR": HTTPStatus.INTERNAL_SERVER_ERROR,
}


class ApiError(Exception):
    """An HTTP-shaped rejection carrying a small, stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DatasetApiServer:
    """Serve `GET /v1/dataset` on one Unix domain socket.

    Owns the `DatasetStore` it is given the same way `CorrelationLoop` owns
    its own: `close()` releases it. The two hold independent connections to
    the same `vision.db` - safe under the WAL mode `DatasetStore.open`
    already enforces, and it keeps this server's lifecycle free of any
    dependency on whether correlation is currently running.
    """

    def __init__(
        self,
        store: DatasetStore,
        *,
        socket_path: str | Path,
        service_token: str,
        minimum_examples_per_class: int,
        socket_mode: int = SOCKET_MODE,
    ) -> None:
        if not service_token:
            raise ValueError("a service token is required; the API is never unauthenticated")
        if minimum_examples_per_class <= 0:
            raise ValueError("minimum_examples_per_class must be positive")
        self._store = store
        self._socket_path = str(socket_path)
        self._token = service_token.encode("utf-8")
        self._minimum = minimum_examples_per_class
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
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=SHUTDOWN_POLL_SECONDS),
            name="cs71vision-api",
            daemon=True,
        )
        self._thread.start()

    def close(self, *, timeout: float = 5.0) -> None:
        server, thread = self._server, self._thread
        self._server, self._thread = None, None
        if server is not None:
            server.shutdown()
            server.server_close()
            if thread is not None:
                thread.join(timeout)
            self._unlink_stale_socket()
        self._store.close()

    def __enter__(self) -> DatasetApiServer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def authenticate(self, header: str | None) -> None:
        scheme, _, presented = (header or "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            presented.strip().encode("utf-8"), self._token
        ):
            raise ApiError("UNAUTHENTICATED", "a valid local service credential is required")

    def route(self, method: str, path: str) -> tuple[HTTPStatus, dict[str, Any]]:
        if method not in {"GET", "HEAD"}:
            raise ApiError("RESOURCE_NOT_FOUND", f"{method} {path} is not available")
        if path == "/v1/dataset":
            return HTTPStatus.OK, self._dataset_body()
        raise ApiError("RESOURCE_NOT_FOUND", f"{path} is not a resource of this API")

    def _dataset_body(self) -> dict[str, Any]:
        counts = self._store.counts_by_slot()
        classes = [
            {"slot": slot, "count": count, "eligible": count >= self._minimum}
            for slot, count in sorted(counts.items())
        ]
        return {
            "api_version": "v1",
            "minimum_examples_per_class": self._minimum,
            "classes": classes,
            "training_ready": any(item["eligible"] for item in classes),
        }

    def _claim_socket_path(self) -> None:
        """Take the socket path only when nothing is already serving it.

        Same reasoning as `cs71d.api.ApiServer._claim_socket_path`: unlinking
        unconditionally would let a second instance silently steal the path
        from a server that is still running.
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
        raise OSError(f"another cs71-vision api is already serving {self._socket_path}")

    def _unlink_stale_socket(self) -> None:
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            return


class _UnixHttpServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    api: DatasetApiServer

    def get_request(self) -> tuple[socket.socket, tuple[str, int]]:
        # BaseHTTPRequestHandler expects an addressable peer; a Unix peer has
        # none, so a stand-in is supplied rather than left to fail.
        request, _ = super().get_request()
        return request, ("unix", 0)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "cs71vision"
    sys_version = ""

    def do_GET(self) -> None:
        self._dispatch("GET")

    def address_string(self) -> str:
        return "unix"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        _LOGGER.debug("cs71vision api %s", format % args)

    def _dispatch(self, method: str) -> None:
        server = self.server
        assert isinstance(server, _UnixHttpServer)
        api = server.api
        try:
            api.authenticate(self.headers.get("Authorization"))
            status, body = api.route(method, self.path)
        except ApiError as exc:
            self._write(_ERROR_STATUS.get(exc.code, HTTPStatus.INTERNAL_SERVER_ERROR), exc)
        except Exception:
            _LOGGER.exception("unhandled cs71vision api failure")
            self._write(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                ApiError("INTERNAL_ERROR", "the vision service could not complete this request"),
            )
        else:
            self._write(status, body)

    def _write(self, status: HTTPStatus, body: ApiError | dict[str, Any]) -> None:
        content = (
            {"code": body.code, "message": body.message} if isinstance(body, ApiError) else body
        )
        payload = json.dumps(content, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

"""cs71-vision's own HTTP/JSON API, served on a Unix domain socket.

Mirrors `cs71d.api`'s shape deliberately, at a fraction of its surface:
dataset review (PI-VISION-003), and training/versioning/activation
(PI-VISION-004/005), authenticated with the same shared bearer credential
every service in this installation already carries
(`cs71vision.runtime.read_service_token`). `cs71-web` is the only intended
caller, the same way it already is for `cs71d`'s socket - see
`docs/architecture/api-and-events.md` for the shared rules (Unix-socket
only, bearer credential, never browser-addressable).

Training, activation and rollback carry no actor or attribution of their
own - this module has no concept of "which operator". Attribution and the
audit trail live entirely on the `cs71-web` side (`recordAudit`), the same
way `cs71d` never learns a browser identity either; this socket only ever
learns "an authenticated caller asked".

This module has no TCP code path at all, the same guarantee
`appliance/daemon/src/cs71d/api.py` makes and tests statically.
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
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Protocol

from .dataset import DatasetError, DatasetStore
from .primer import requires_operator_confirmation

_LOGGER = logging.getLogger("cs71vision.api")

SOCKET_MODE = 0o660
SHUTDOWN_POLL_SECONDS = 0.2
#: None of this API's routes have a body to read; this only bounds how much
#: of an unexpected one is ever drained before being discarded.
MAX_BODY_BYTES = 4_096

_ACTIVATE_PATH = re.compile(r"^/v1/models/(?P<version>[0-9]+)/activate$")

_ERROR_STATUS: dict[str, HTTPStatus] = {
    "UNAUTHENTICATED": HTTPStatus.UNAUTHORIZED,
    "RESOURCE_NOT_FOUND": HTTPStatus.NOT_FOUND,
    "VALIDATION_FAILED": HTTPStatus.BAD_REQUEST,
    "INTERNAL_ERROR": HTTPStatus.INTERNAL_SERVER_ERROR,
}


class ApiError(Exception):
    """An HTTP-shaped rejection carrying a small, stable error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Trainable(Protocol):
    """What this API needs to trigger training - `TrainingJob` today, a fake in tests.

    A `Protocol`, not a concrete import of `cs71vision.runtime.TrainingJob`:
    `runtime.py` builds a `VisionApiServer` (`build_api_server`), so a
    concrete import here would be circular. `TrainingJob` satisfies this
    structurally, the same way `Correlator` satisfies `runtime.Poller`
    without either module importing the other.
    """

    def trigger(self) -> bool: ...
    def close(self) -> None: ...


class VisionApiServer:
    """Serve `cs71-vision`'s dataset/training/activation resources on one socket.

    Owns both the `DatasetStore` and the `TrainingJob` it is given, the same
    way `CorrelationLoop` owns its own store: `close()` releases both. Each
    holds an independent connection to the same `vision.db` - safe under the
    WAL mode `DatasetStore.open` already enforces, and it keeps every
    component's lifecycle free of any dependency on the others' state.
    """

    def __init__(
        self,
        store: DatasetStore,
        *,
        socket_path: str | Path,
        service_token: str,
        minimum_examples_per_class: int,
        training_job: Trainable,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
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
        self._training_job = training_job
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
        self._training_job.close()
        self._store.close()

    def __enter__(self) -> VisionApiServer:
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
        if method in {"GET", "HEAD"}:
            if path == "/v1/dataset":
                return HTTPStatus.OK, self._dataset_body()
            if path == "/v1/models":
                return HTTPStatus.OK, self._models_body()
            if path == "/v1/suggestion":
                return HTTPStatus.OK, self._suggestion_body()
            if path == "/v1/suggestion-accuracy":
                return HTTPStatus.OK, self._suggestion_accuracy_body()
            raise ApiError("RESOURCE_NOT_FOUND", f"{path} is not a resource of this API")
        if method == "POST":
            if path == "/v1/train":
                return HTTPStatus.OK, self._train_body()
            if path == "/v1/rollback":
                return HTTPStatus.OK, self._rollback_body()
            matched = _ACTIVATE_PATH.fullmatch(path)
            if matched is not None:
                return HTTPStatus.OK, self._activate_body(int(matched.group("version")))
            raise ApiError("RESOURCE_NOT_FOUND", f"POST {path} is not a resource of this API")
        raise ApiError("RESOURCE_NOT_FOUND", f"{method} {path} is not available")

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

    def _models_body(self) -> dict[str, Any]:
        return {
            "api_version": "v1",
            "active_version": self._store.active_version(),
            # Mirrors _rollback_body's own precondition exactly, so a caller
            # can show or hide a rollback control correctly rather than
            # guessing from candidate count (a model can be trained many
            # times while only ever activated once, which leaves nothing to
            # roll back to despite several recorded candidates existing).
            "can_roll_back": len(self._store.activations()) >= 2,
            "candidates": [
                {
                    "version": candidate.version,
                    "trained_at": candidate.trained_at,
                    "included_classes": list(candidate.included_classes),
                    "excluded_classes": list(candidate.excluded_classes),
                    "accuracy_by_class": {
                        str(slot): accuracy
                        for slot, accuracy in candidate.accuracy_by_class.items()
                    },
                    "minimum_examples_per_class": candidate.minimum_examples_per_class,
                    "training_example_count": candidate.training_example_count,
                    "holdout_example_count": candidate.holdout_example_count,
                }
                for candidate in self._store.candidates()
            ],
        }

    def _suggestion_body(self) -> dict[str, Any]:
        """The most recently recorded suggestion, or `null` when there is none yet.

        A pure read: this never classifies on demand and never issues a
        `cs71d` command, under any configuration. `runtime.SuggestionLoop`
        is the only thing that ever writes a suggestion.
        """
        suggestion = self._store.latest_suggestion()
        if suggestion is None:
            return {"api_version": "v1", "suggestion": None}
        return {
            "api_version": "v1",
            "suggestion": {
                "slot": suggestion.suggested_slot,
                "confidence": suggestion.confidence,
                "model_version": suggestion.model_version,
                "suggested_at": suggestion.suggested_at,
                # Primer-presence axis (PI-VISION-010): permanently separate
                # from slot/confidence. `requires_confirmation` is the single
                # source of truth for this axis - `cs71vision.primer` - so no
                # caller ever needs to reimplement "None or True means ask a
                # person" for itself.
                "primer_present": suggestion.primer_present,
                "requires_confirmation": requires_operator_confirmation(suggestion.primer_present),
            },
        }

    def _suggestion_accuracy_body(self) -> dict[str, Any]:
        """Live suggestion accuracy - separate evidence from training-time held-out accuracy."""
        accuracy = self._store.suggestion_accuracy()
        return {
            "api_version": "v1",
            "total": accuracy.total,
            "correct": accuracy.correct,
            "accuracy": accuracy.fraction,
        }

    def _train_body(self) -> dict[str, Any]:
        """Trigger a training run. `started: false` means one was already in flight.

        Never itself the floor-and-classes decision - `TrainingJob.trigger()`
        starting nothing there is already sufficient answer to "was a run
        already running"; the caller does not need a distinct error for it.
        """
        return {"api_version": "v1", "started": self._training_job.trigger()}

    def _activate_body(self, version: int) -> dict[str, Any]:
        activated_at = self._now()
        try:
            self._store.activate(version, activated_at=activated_at)
        except DatasetError as exc:
            raise ApiError("RESOURCE_NOT_FOUND", str(exc)) from exc
        return {
            "api_version": "v1",
            "active_version": version,
            "activated_at": _rfc3339(activated_at),
        }

    def _rollback_body(self) -> dict[str, Any]:
        history = self._store.activations()
        if len(history) < 2:
            raise ApiError("VALIDATION_FAILED", "there is no previous version to roll back to")
        previous_version = history[-2].version
        activated_at = self._now()
        self._store.activate(previous_version, activated_at=activated_at)
        return {
            "api_version": "v1",
            "active_version": previous_version,
            "activated_at": _rfc3339(activated_at),
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


def _rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


class _UnixHttpServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    api: VisionApiServer

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

    def do_POST(self) -> None:
        self._dispatch("POST")

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
            self._drain_body()
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

    def _drain_body(self) -> None:
        """Read and discard any request body, capped, so the connection stays clean.

        No route on this API parses a body; this exists only so an
        HTTP/1.1 keep-alive connection is not left with unread bytes ahead
        of whatever the client sends next.
        """
        declared = self.headers.get("Content-Length")
        if declared is None:
            return
        try:
            length = min(int(declared), MAX_BODY_BYTES)
        except ValueError:
            return
        if length > 0:
            self.rfile.read(length)

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

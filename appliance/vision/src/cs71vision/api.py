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
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Protocol

from .dataset import DatasetError, DatasetStore
from .primer import requires_operator_confirmation
from .routing import (
    DynamicProfile,
    FixedMapProfile,
    RoutingError,
    RoutingProfile,
    RoutingSession,
    TwoPassProfile,
)

_LOGGER = logging.getLogger("cs71vision.api")

SOCKET_MODE = 0o660
SHUTDOWN_POLL_SECONDS = 0.2
#: None of this API's routes have a body to read; this only bounds how much
#: of an unexpected one is ever drained before being discarded.
MAX_BODY_BYTES = 4_096

_ACTIVATE_PATH = re.compile(r"^/v1/models/(?P<version>[0-9]+)/activate$")
_EMPTY_THRESHOLDS: Mapping[int, float] = {}

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
        autonomy_thresholds: Mapping[int, float] = _EMPTY_THRESHOLDS,
        routing: RoutingSession | None = None,
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
        self._autonomy_thresholds = autonomy_thresholds
        # A private session when none is given, so this server is always
        # usable standalone (tests, or PI-VISION-009's own routes without a
        # runtime.py-wired suggestion loop) - `runtime.build_api_server`
        # always passes the same instance its suggestion loop routes
        # against, so a run started here is a run that actually applies.
        self._routing = routing if routing is not None else RoutingSession()
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

    def route(self, method: str, path: str, body: bytes = b"") -> tuple[HTTPStatus, dict[str, Any]]:
        if method in {"GET", "HEAD"}:
            if path == "/v1/dataset":
                return HTTPStatus.OK, self._dataset_body()
            if path == "/v1/models":
                return HTTPStatus.OK, self._models_body()
            if path == "/v1/suggestion":
                return HTTPStatus.OK, self._suggestion_body()
            if path == "/v1/suggestion-accuracy":
                return HTTPStatus.OK, self._suggestion_accuracy_body()
            if path == "/v1/autonomy":
                return HTTPStatus.OK, self._autonomy_body()
            if path == "/v1/routing":
                return HTTPStatus.OK, self._routing_body()
            raise ApiError("RESOURCE_NOT_FOUND", f"{path} is not a resource of this API")
        if method == "POST":
            if path == "/v1/train":
                return HTTPStatus.OK, self._train_body()
            if path == "/v1/rollback":
                return HTTPStatus.OK, self._rollback_body()
            if path == "/v1/autonomous-reviews":
                return HTTPStatus.OK, self._autonomous_review_body(body)
            if path == "/v1/routing/start":
                return HTTPStatus.OK, self._routing_start_body(body)
            if path == "/v1/routing/stop":
                return HTTPStatus.OK, self._routing_stop_body()
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

    def _autonomy_body(self) -> dict[str, Any]:
        """Configured per-class thresholds, reviewed accuracy, and the review queue.

        The false-autonomous-sort rate a class's threshold may only be
        lowered after seeing (ADR-0013) - a class with attempts but no
        review yet is absent from `accuracy_by_class`, never reported as 0%
        false: "unreviewed" and "reviewed clean" must never look the same.
        """
        accuracy = {
            str(entry.slot): {
                "total": entry.total,
                "correct": entry.correct,
                "false_rate": entry.false_rate,
            }
            for entry in self._store.autonomous_accuracy_by_class()
        }
        pending = [
            {
                "attempt_id": attempt.attempt_id,
                "suggestion_id": attempt.suggestion_id,
                "operation_id": attempt.operation_id,
                "slot": attempt.slot,
                "attempted_at": attempt.attempted_at,
            }
            for attempt in self._store.pending_autonomous_reviews()
        ]
        return {
            "api_version": "v1",
            "thresholds": {str(slot): value for slot, value in self._autonomy_thresholds.items()},
            "accuracy_by_class": accuracy,
            "pending_review": pending,
        }

    def _autonomous_review_body(self, body: bytes) -> dict[str, Any]:
        """Record a human's verdict on one autonomous attempt.

        The only source of ground truth for an autonomous sort: there is no
        operator confirmation step to compare against the way
        `suggestion_outcomes` compares a manual sort to its suggestion, so
        this is never inferred, only recorded from what a person reports
        after actually checking (ADR-0013's own "not a general 'it seems to
        work'").
        """
        payload = _json_object(body)
        if set(payload) != {"api_version", "attempt_id", "correct"}:
            raise ApiError(
                "VALIDATION_FAILED",
                "an autonomous review carries exactly api_version, attempt_id and correct",
            )
        attempt_id = payload["attempt_id"]
        correct = payload["correct"]
        if isinstance(attempt_id, bool) or not isinstance(attempt_id, int):
            raise ApiError("VALIDATION_FAILED", "attempt_id must be an integer")
        if not isinstance(correct, bool):
            raise ApiError("VALIDATION_FAILED", "correct must be a boolean")
        reviewed_at = self._now()
        try:
            self._store.record_autonomous_review(
                attempt_id=attempt_id, correct=correct, reviewed_at=reviewed_at
            )
        except DatasetError as exc:
            raise ApiError("RESOURCE_NOT_FOUND", str(exc)) from exc
        return {
            "api_version": "v1",
            "attempt_id": attempt_id,
            "correct": correct,
            "reviewed_at": _rfc3339(reviewed_at),
        }

    def _routing_body(self) -> dict[str, Any]:
        """The active routing run, if any, and its live chute<->class legend."""
        snapshot = self._routing.snapshot()
        source_group = (
            snapshot.profile.source_group if isinstance(snapshot.profile, TwoPassProfile) else None
        )
        return {
            "api_version": "v1",
            "active": snapshot.active,
            "kind": snapshot.profile.kind if snapshot.profile is not None else None,
            "started_at": snapshot.started_at,
            "source_group": source_group,
            "legend": [
                {"slot": entry.slot, "class_id": entry.class_id, "overflow": entry.overflow}
                for entry in snapshot.legend
            ],
        }

    def _routing_start_body(self, body: bytes) -> dict[str, Any]:
        """Start a new routing run, replacing any previous one entirely (PI-VISION-009)."""
        payload = _json_object(body)
        profile = _routing_profile_from(payload)
        try:
            self._routing.start(profile)
        except RoutingError as exc:
            raise ApiError("VALIDATION_FAILED", str(exc)) from exc
        return self._routing_body()

    def _routing_stop_body(self) -> dict[str, Any]:
        """End the active routing run. A no-op, not an error, when none is active."""
        self._routing.stop()
        return self._routing_body()

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


def _json_object(body: bytes) -> dict[str, Any]:
    if not body:
        raise ApiError("VALIDATION_FAILED", "a JSON request body is required")
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise ApiError("VALIDATION_FAILED", "the request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ApiError("VALIDATION_FAILED", "the request body must be a JSON object")
    if payload.get("api_version") != "v1":
        raise ApiError("VALIDATION_FAILED", "api_version must be 'v1'")
    return payload


def _routing_profile_from(payload: Mapping[str, Any]) -> RoutingProfile:
    kind = payload.get("kind")
    if kind == "fixed":
        _require_fields(
            payload, {"api_version", "kind", "class_to_slot", "overflow_slot"}, (), "fixed"
        )
        return FixedMapProfile(
            class_to_slot=_class_to_slot(payload["class_to_slot"]),
            overflow_slot=_int_field(payload["overflow_slot"], "overflow_slot"),
        )
    if kind == "dynamic":
        _require_fields(payload, {"api_version", "kind", "available_slots"}, (), "dynamic")
        return DynamicProfile(available_slots=_slot_list(payload["available_slots"]))
    if kind == "two_pass":
        _require_fields(
            payload,
            {"api_version", "kind", "class_to_slot", "overflow_slot"},
            ("source_group",),
            "two-pass",
        )
        source_group = payload.get("source_group")
        return TwoPassProfile(
            class_to_slot=_class_to_slot(payload["class_to_slot"]),
            overflow_slot=_int_field(payload["overflow_slot"], "overflow_slot"),
            source_group=None if source_group is None else _int_field(source_group, "source_group"),
        )
    raise ApiError("VALIDATION_FAILED", "kind must be 'fixed', 'dynamic' or 'two_pass'")


def _require_fields(
    payload: Mapping[str, Any], required: set[str], optional: tuple[str, ...], name: str
) -> None:
    allowed = required | set(optional)
    if not required <= set(payload) or not set(payload) <= allowed:
        raise ApiError(
            "VALIDATION_FAILED", f"a {name} routing profile carries exactly {sorted(required)}"
        )


def _class_to_slot(raw: Any) -> dict[int, int]:
    if not isinstance(raw, dict) or not raw:
        raise ApiError("VALIDATION_FAILED", "class_to_slot must be a non-empty object")
    result: dict[int, int] = {}
    for key, value in raw.items():
        try:
            class_id = int(key)
        except (TypeError, ValueError):
            raise ApiError("VALIDATION_FAILED", "class_to_slot keys must be integers") from None
        result[class_id] = _int_field(value, "class_to_slot")
    return result


def _slot_list(raw: Any) -> tuple[int, ...]:
    if not isinstance(raw, list) or not raw:
        raise ApiError("VALIDATION_FAILED", "available_slots must be a non-empty list")
    return tuple(_int_field(item, "available_slots") for item in raw)


def _int_field(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ApiError("VALIDATION_FAILED", f"{name} must be an integer")
    return value


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
            request_body = self._read_body()
            status, body = api.route(method, self.path, request_body)
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

    def _read_body(self) -> bytes:
        """Read and return any request body, capped.

        Most routes on this API never parse a body and simply discard what
        this returns - `_autonomous_review_body` (PI-VISION-008) is the
        first that does. Capped either way, so an HTTP/1.1 keep-alive
        connection is never left with unread bytes ahead of whatever the
        client sends next.
        """
        declared = self.headers.get("Content-Length")
        if declared is None:
            return b""
        try:
            length = min(int(declared), MAX_BODY_BYTES)
        except ValueError:
            return b""
        return self.rfile.read(length) if length > 0 else b""

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

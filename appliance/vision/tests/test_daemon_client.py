from __future__ import annotations

import json
import shutil
import socketserver
import tempfile
import threading
from collections.abc import Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import pytest

from cs71vision.daemon_client import DaemonClient, DaemonClientError

TOKEN = "cs71-vision-test-credential"  # noqa: S105 - test fixture, not a secret


class _FakeServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True
    routes: dict[str, tuple[int, Any]]
    post_routes: dict[str, tuple[int, Any]]
    require_token: str | None
    last_request: dict[str, Any] | None


class _FakeHandler(BaseHTTPRequestHandler):
    server: _FakeServer

    def log_message(self, *args: object) -> None:  # quiet the test output
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server's own naming convention
        self._serve(self.server.routes)

    def do_POST(self) -> None:  # noqa: N802 - http.server's own naming convention
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        self.server.last_request = {
            "path": self.path,
            "headers": dict(self.headers.items()),
            "body": json.loads(raw_body) if raw_body else None,
        }
        self._serve(self.server.post_routes)

    def _serve(self, routes: dict[str, tuple[int, Any]]) -> None:
        if self.server.require_token is not None:
            presented = self.headers.get("Authorization", "")
            if presented != f"Bearer {self.server.require_token}":
                self._respond(HTTPStatus.UNAUTHORIZED, {"code": "UNAUTHENTICATED"})
                return
        if self.path not in routes:
            self._respond(HTTPStatus.NOT_FOUND, {"code": "RESOURCE_NOT_FOUND"})
            return
        status, body = routes[self.path]
        self._respond(status, body)

    def _respond(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture
def fake_daemon() -> Iterator[tuple[str, _FakeServer]]:
    directory = Path(tempfile.mkdtemp(prefix="cs71vision-client"))
    socket_path = str(directory / "cs71d.sock")
    server = _FakeServer(socket_path, _FakeHandler)
    server.routes = {}
    server.post_routes = {}
    server.require_token = TOKEN
    server.last_request = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield socket_path, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
        shutil.rmtree(directory, ignore_errors=True)


def _operation(
    *, operation_id: str, slot: int | None, op_type: str = "SORT", state: str = "SUCCEEDED"
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "operation_id": operation_id,
        "type": op_type,
        "state": state,
        "created_at": "2026-08-12T12:00:00Z",
    }
    if slot is not None:
        body["terminal_fields"] = {"slot": str(slot)}
    return body


def test_recent_sort_successes_parses_the_slot_from_terminal_fields(
    fake_daemon: tuple[str, _FakeServer],
) -> None:
    socket_path, server = fake_daemon
    server.routes["/v1/operations?type=SORT&state=SUCCEEDED&limit=25"] = (
        200,
        {"api_version": "v1", "items": [_operation(operation_id="op-1", slot=3)]},
    )
    client = DaemonClient(socket_path, TOKEN)

    successes = client.recent_sort_successes()

    assert len(successes) == 1
    assert successes[0].operation_id == "op-1"
    assert successes[0].slot == 3
    assert successes[0].created_at == "2026-08-12T12:00:00Z"


def test_recent_sort_successes_presents_its_own_bearer_credential(
    fake_daemon: tuple[str, _FakeServer],
) -> None:
    socket_path, server = fake_daemon
    server.routes["/v1/operations?type=SORT&state=SUCCEEDED&limit=25"] = (
        200,
        {"api_version": "v1", "items": []},
    )
    client = DaemonClient(socket_path, "wrong-token")

    with pytest.raises(DaemonClientError, match="HTTP 401"):
        client.recent_sort_successes()


@pytest.mark.parametrize(
    "item",
    [
        {"operation_id": "op-1", "type": "HOME", "state": "SUCCEEDED", "created_at": "x"},
        {"operation_id": "op-1", "type": "SORT", "state": "FAILED", "created_at": "x"},
        {"operation_id": "op-1", "type": "SORT", "state": "SUCCEEDED", "created_at": "x"},
        {
            "operation_id": "op-1",
            "type": "SORT",
            "state": "SUCCEEDED",
            "created_at": "x",
            "terminal_fields": {"slot": "not-a-number"},
        },
        "not-an-object",
    ],
)
def test_recent_sort_successes_skips_entries_without_a_usable_slot(
    fake_daemon: tuple[str, _FakeServer],
    item: Any,
) -> None:
    socket_path, server = fake_daemon
    server.routes["/v1/operations?type=SORT&state=SUCCEEDED&limit=25"] = (
        200,
        {"api_version": "v1", "items": [item]},
    )
    client = DaemonClient(socket_path, TOKEN)

    assert client.recent_sort_successes() == ()


def test_a_malformed_page_without_items_is_a_client_error(
    fake_daemon: tuple[str, _FakeServer],
) -> None:
    socket_path, server = fake_daemon
    server.routes["/v1/operations?type=SORT&state=SUCCEEDED&limit=25"] = (
        200,
        {"api_version": "v1"},
    )
    client = DaemonClient(socket_path, TOKEN)

    with pytest.raises(DaemonClientError, match="no items"):
        client.recent_sort_successes()


def test_an_unreachable_socket_is_a_client_error(tmp_path: Path) -> None:
    client = DaemonClient(str(tmp_path / "does-not-exist.sock"), TOKEN)

    with pytest.raises(DaemonClientError, match="cannot reach"):
        client.recent_sort_successes()


def test_constructing_a_client_requires_a_service_token() -> None:
    with pytest.raises(ValueError, match="service token"):
        DaemonClient("/tmp/whatever.sock", "")


def test_constructing_a_client_refuses_an_empty_machine_token() -> None:
    with pytest.raises(ValueError, match="machine_service_token"):
        DaemonClient("/tmp/whatever.sock", TOKEN, machine_service_token="")


def test_current_generation_reads_the_snapshot(fake_daemon: tuple[str, _FakeServer]) -> None:
    socket_path, server = fake_daemon
    server.routes["/v1/snapshot"] = (200, {"api_version": "v1", "generation": 41})
    client = DaemonClient(socket_path, TOKEN)

    assert client.current_generation() == 41


def test_current_generation_is_a_client_error_without_a_usable_generation(
    fake_daemon: tuple[str, _FakeServer],
) -> None:
    socket_path, server = fake_daemon
    server.routes["/v1/snapshot"] = (200, {"api_version": "v1"})
    client = DaemonClient(socket_path, TOKEN)

    with pytest.raises(DaemonClientError, match="no usable generation"):
        client.current_generation()


def test_submit_sort_refuses_without_a_configured_machine_credential(
    fake_daemon: tuple[str, _FakeServer],
) -> None:
    socket_path, _server = fake_daemon
    client = DaemonClient(socket_path, TOKEN)  # no machine_service_token

    with pytest.raises(DaemonClientError, match="no machine service credential"):
        client.submit_sort(slot=3, generation=41, idempotency_key="a" * 16)


MACHINE_TOKEN = "cs71-vision-machine-credential"  # noqa: S105 - test fixture, not a secret


def test_submit_sort_presents_the_machine_credential_as_the_machine_role(
    fake_daemon: tuple[str, _FakeServer],
) -> None:
    socket_path, server = fake_daemon
    server.require_token = MACHINE_TOKEN
    server.post_routes["/v1/operations/sort"] = (
        202,
        {"api_version": "v1", "operation_id": "op-1", "state": "ACCEPTED", "generation": 42},
    )
    client = DaemonClient(socket_path, TOKEN, machine_service_token=MACHINE_TOKEN)

    operation_id = client.submit_sort(slot=3, generation=41, idempotency_key="a" * 16)

    assert operation_id == "op-1"
    assert server.last_request is not None
    assert server.last_request["headers"]["Authorization"] == f"Bearer {MACHINE_TOKEN}"
    assert server.last_request["headers"]["Idempotency-Key"] == "a" * 16
    assert server.last_request["headers"]["If-Match-Generation"] == "41"
    assert server.last_request["headers"]["X-Deadline-Ms"] == "15000"
    assert server.last_request["body"] == {
        "api_version": "v1",
        "actor": {"user_id": "cs71-vision", "role": "machine"},
        "slot": 3,
    }


def test_submit_sort_never_presents_the_ordinary_shared_credential(
    fake_daemon: tuple[str, _FakeServer],
) -> None:
    socket_path, server = fake_daemon
    server.require_token = MACHINE_TOKEN  # the fake daemon only accepts the machine credential
    server.post_routes["/v1/operations/sort"] = (
        202,
        {"api_version": "v1", "operation_id": "op-1", "state": "ACCEPTED", "generation": 42},
    )
    # A wrong ordinary token proves submit_sort never falls back to it.
    client = DaemonClient(socket_path, "wrong-ordinary-token", machine_service_token=MACHINE_TOKEN)

    operation_id = client.submit_sort(slot=3, generation=41, idempotency_key="a" * 16)

    assert operation_id == "op-1"


def test_submit_sort_is_a_client_error_on_rejection(fake_daemon: tuple[str, _FakeServer]) -> None:
    socket_path, server = fake_daemon
    server.require_token = MACHINE_TOKEN
    server.post_routes["/v1/operations/sort"] = (409, {"code": "STALE_GENERATION"})
    client = DaemonClient(socket_path, TOKEN, machine_service_token=MACHINE_TOKEN)

    with pytest.raises(DaemonClientError, match="HTTP 409"):
        client.submit_sort(slot=3, generation=41, idempotency_key="a" * 16)

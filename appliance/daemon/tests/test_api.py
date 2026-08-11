from __future__ import annotations

import ast
import http.client
import json
import shutil
import socket
import stat
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from contract import assert_conforms

from cs71d.api import ApiServer
from cs71d.domain import OperationDomain, WorkerObservers
from cs71d.journal import IN_MEMORY, Journal
from cs71d.operations import Actor, OperationAction, OperationRecord, OperationState
from cs71d.serial_worker import SerialWorker
from cs71d.simulator import SimulatorConfig, SimulatorTransport

TOKEN = "installation-local-service-credential"  # noqa: S105 - test fixture, not a secret
NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
OPERATOR = Actor(user_id="opaque-bff-attribution", role="operator")


@dataclass(slots=True)
class Response:
    status: int
    headers: dict[str, str]
    body: Any


class UnixClient:
    """Speak HTTP to a Unix domain socket, the way the local BFF will."""

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = TOKEN,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> Response:
        connection = _UnixConnection(self._socket_path)
        try:
            sent = dict(headers or {})
            if token is not None:
                sent.setdefault("Authorization", f"Bearer {token}")
            connection.request(method, path, body=body, headers=sent)
            raw = connection.getresponse()
            payload = raw.read()
            return Response(
                raw.status,
                {name.lower(): value for name, value in raw.getheaders()},
                json.loads(payload) if payload else None,
            )
        finally:
            connection.close()


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost", timeout=5.0)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect(self._socket_path)


@dataclass(slots=True)
class ApiHarness:
    client: UnixClient
    domain: OperationDomain
    journal: Journal
    server: ApiServer
    simulator: SimulatorTransport

    def home(self) -> OperationRecord:
        record = self.domain.submit(
            OperationAction.HOME,
            {"axis": "both"},
            actor=OPERATOR,
            idempotency_key="home-1",
            expected_generation=self.domain.snapshot.generation,
            deadline_ms=5_000,
        )
        assert self.simulator.wait_until_scheduled(timeout=1.0)
        self.simulator.advance(10_000)
        return record


@pytest.fixture
def make_api() -> Iterator[Callable[..., ApiHarness]]:
    # A Unix socket path is limited to about 100 bytes, which pytest's own
    # temporary directory names can exceed, so keep the directory short.
    directory = Path(tempfile.mkdtemp(prefix="cs71d"))
    built: list[ApiHarness] = []

    def make(*, start: bool = True) -> ApiHarness:
        simulator = SimulatorTransport(SimulatorConfig())
        journal = Journal.open(IN_MEMORY, now=lambda: NOW)

        def worker_factory(observers: WorkerObservers) -> SerialWorker:
            return SerialWorker(
                lambda: simulator,
                protocol_timeout=0.1,
                session_observer=observers.session,
                profile_observer=observers.profile,
            )

        domain = OperationDomain(journal, worker_factory, now=lambda: NOW)
        if start:
            domain.start(timeout=1.0)
        server = ApiServer(
            domain,
            journal,
            socket_path=directory / f"{len(built)}.sock",
            service_token=TOKEN,
            now=lambda: NOW,
        )
        server.start()
        harness = ApiHarness(UnixClient(server.socket_path), domain, journal, server, simulator)
        built.append(harness)
        return harness

    yield make

    for harness in built:
        harness.server.close()
        harness.domain.close(timeout=1.0)
        harness.journal.close()
    shutil.rmtree(directory, ignore_errors=True)


def test_the_daemon_listens_on_a_unix_socket_with_owner_and_group_access_only(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    mode = Path(harness.server.socket_path).stat().st_mode

    assert stat.S_ISSOCK(mode)
    assert stat.S_IMODE(mode) == 0o660
    # Browser users are not in the daemon's group, and there is no other door.
    assert not stat.S_IMODE(mode) & stat.S_IRWXO


def test_no_daemon_module_can_open_an_internet_listener() -> None:
    """A TCP listener cannot be misconfigured into existence; there is no code for it."""
    package_root = Path(__file__).parents[1] / "src/cs71d"
    offenders: dict[str, set[str]] = {}
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        named = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and node.attr in {"AF_INET", "AF_INET6", "create_server", "create_connection"}
        }
        if named:
            offenders[str(path.relative_to(package_root))] = named

    assert offenders == {}


def test_liveness_answers_without_asserting_anything_about_the_controller(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api(start=False)

    response = harness.client.request("GET", "/v1/health/live")

    assert response.status == 200
    assert_conforms(response.body, "Liveness")
    assert response.headers["x-request-id"]
    assert response.headers["cache-control"] == "no-store"


def test_readiness_reports_the_session_rather_than_liveness(
    make_api: Callable[..., ApiHarness],
) -> None:
    stopped = make_api(start=False)

    unready = stopped.client.request("GET", "/v1/health/ready")

    assert unready.status == 200
    assert_conforms(unready.body, "Readiness")
    assert unready.body["ready"] is False
    assert unready.body["connection_state"] == "DISCONNECTED"
    assert unready.body["reason"]

    running = make_api()
    ready = running.client.request("GET", "/v1/health/ready")

    assert ready.body["ready"] is True
    assert ready.body["connection_state"] == "READY"
    assert ready.body["reason"] is None


def test_the_snapshot_conforms_and_carries_its_generation_as_an_etag(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = harness.client.request("GET", "/v1/snapshot")

    assert response.status == 200
    assert_conforms(response.body, "MachineSnapshot")
    assert response.headers["etag"] == f'"generation:{response.body["generation"]}"'
    assert response.body["firmware"]["capabilities"]["v2_available"] is True
    assert response.body["firmware"]["capabilities"]["feed_available"] is False
    assert response.body["machine"]["sort_homed"] is False
    assert response.body["faults"] == []


def test_an_unobserved_controller_advertises_nothing(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api(start=False)

    body = harness.client.request("GET", "/v1/snapshot").body

    assert_conforms(body, "MachineSnapshot")
    assert body["firmware"]["protocol_version"] == 1
    assert body["firmware"]["capabilities"] == {
        "v2_available": False,
        "crc_active": False,
        "slot_count": 1,
        "home_available": False,
        "sort_available": False,
        "feed_available": False,
        "feed_unavailable_reason": "the v2 feed lifecycle gate is NOT_EXECUTED",
    }
    assert body["ready"] is False


def test_a_completed_operation_is_readable_and_conforms(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    admitted = harness.home()

    response = harness.client.request("GET", f"/v1/operations/{admitted.operation_id}")

    assert response.status == 200
    assert_conforms(response.body, "Operation")
    assert response.body["type"] == "HOME"
    assert response.body["actor"] == {"user_id": OPERATOR.user_id, "role": OPERATOR.role}


def test_an_unknown_operation_is_not_found(make_api: Callable[..., ApiHarness]) -> None:
    harness = make_api()

    response = harness.client.request("GET", "/v1/operations/00000000-0000-4000-8000-000000000000")

    assert response.status == 404
    assert_conforms(response.body, "NotFoundError")


def test_the_operation_history_is_bounded_filterable_and_paged(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    harness.home()

    page = harness.client.request("GET", "/v1/operations?limit=1")

    assert page.status == 200
    assert_conforms(page.body, "OperationPage")
    assert len(page.body["items"]) == 1
    filtered = harness.client.request("GET", "/v1/operations?type=SORT")
    assert filtered.body["items"] == []
    by_state = harness.client.request("GET", "/v1/operations?state=SUCCEEDED")
    assert [item["state"] for item in by_state.body["items"]] == ["SUCCEEDED"]


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=many", "cursor=abc"])
def test_a_malformed_query_is_a_validation_failure(
    make_api: Callable[..., ApiHarness],
    query: str,
) -> None:
    harness = make_api()

    response = harness.client.request("GET", f"/v1/operations?{query}")

    assert response.status == 400
    assert_conforms(response.body, "ValidationError")


@pytest.mark.parametrize("token", [None, "wrong-token", ""])
def test_every_resource_requires_the_local_service_credential(
    make_api: Callable[..., ApiHarness],
    token: str | None,
) -> None:
    harness = make_api()

    response = harness.client.request("GET", "/v1/snapshot", token=token)

    assert response.status == 401
    assert_conforms(response.body, "UnauthenticatedError")
    assert "etag" not in response.headers


def test_an_unknown_resource_and_an_unavailable_method_are_reported_as_errors(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    unknown = harness.client.request("GET", "/v1/nope")
    unavailable = harness.client.request("POST", "/v1/operations/sort", body=b"{}")

    assert unknown.status == 404
    assert_conforms(unknown.body, "NotFoundError")
    assert unavailable.status == 404
    assert_conforms(unavailable.body, "NotFoundError")


def test_a_supplied_request_id_is_echoed_and_a_missing_one_is_generated(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    supplied = "11111111-2222-4333-8444-555555555555"

    echoed = harness.client.request("GET", "/v1/health/live", headers={"X-Request-ID": supplied})
    generated = harness.client.request("GET", "/v1/health/live")

    assert echoed.headers["x-request-id"] == supplied
    assert generated.headers["x-request-id"] != supplied


def test_an_oversized_body_is_refused_without_being_read(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = harness.client.request(
        "GET",
        "/v1/health/live",
        headers={"Content-Length": str(1024 * 1024)},
    )

    assert response.status == 400
    assert_conforms(response.body, "ValidationError")


def test_a_journal_failure_is_reported_as_service_unavailable(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    harness.journal.close()

    response = harness.client.request("GET", "/v1/operations")

    assert response.status == 503
    assert_conforms(response.body, "UnavailableError")
    assert "closed" in response.body["message"]


def test_an_error_body_never_carries_protocol_internals(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = harness.client.request("GET", "/v1/operations/not-a-uuid")

    assert response.status == 404
    assert_conforms(response.body, "NotFoundError")
    assert set(response.body) <= {
        "api_version",
        "code",
        "message",
        "request_id",
        "generation",
        "details",
    }
    assert "request_id" in response.body
    assert response.body["code"] == "RESOURCE_NOT_FOUND"
    assert OperationState.QUEUED.value not in json.dumps(response.body)

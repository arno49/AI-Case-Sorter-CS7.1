from __future__ import annotations

import ast
import http.client
import json
import shutil
import socket
import stat
import tempfile
import threading
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


class TerminalWatcher:
    """Await published terminals instead of polling the durable record.

    The domain records a terminal on the worker thread after the worker
    resolves its own future, so anything else would race the transition.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._records: list[OperationRecord] = []

    def observe(self, record: OperationRecord) -> None:
        with self._condition:
            self._records.append(record)
            self._condition.notify_all()

    def await_terminal(self, operation_id: str, *, timeout: float = 3.0) -> OperationRecord:
        def matched() -> OperationRecord | None:
            return next(
                (
                    record
                    for record in self._records
                    if record.operation_id == operation_id and record.is_terminal
                ),
                None,
            )

        with self._condition:
            found = self._condition.wait_for(matched, timeout)
        if found is None:
            raise AssertionError(f"operation {operation_id} never reached a terminal state")
        return found


@dataclass(slots=True)
class ApiHarness:
    client: UnixClient
    domain: OperationDomain
    journal: Journal
    server: ApiServer
    simulators: list[SimulatorTransport]
    terminals: TerminalWatcher

    @property
    def simulator(self) -> SimulatorTransport:
        """The transport the worker is currently using."""
        return self.simulators[-1]

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
        simulators: list[SimulatorTransport] = []
        journal = Journal.open(IN_MEMORY, now=lambda: NOW)

        def open_transport() -> SimulatorTransport:
            # A reconnect must get a fresh transport; a closed one stays closed.
            simulators.append(SimulatorTransport(SimulatorConfig()))
            return simulators[-1]

        def worker_factory(observers: WorkerObservers) -> SerialWorker:
            return SerialWorker(
                open_transport,
                protocol_timeout=0.1,
                session_observer=observers.session,
                profile_observer=observers.profile,
            )

        terminals = TerminalWatcher()
        domain = OperationDomain(
            journal, worker_factory, now=lambda: NOW, operation_observer=terminals.observe
        )
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
        harness = ApiHarness(
            UnixClient(server.socket_path), domain, journal, server, simulators, terminals
        )
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
    # Configuration is in the contract but has no domain behind it yet, so the
    # daemon reports it as absent rather than pretending to accept a change.
    unavailable = harness.client.request("PATCH", "/v1/configuration", body=b"{}")

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


COMMAND_HEADERS = {
    "Idempotency-Key": "bff-idempotency-key-0001",
    "If-Match-Generation": "5",
    "X-Deadline-Ms": "5000",
    "Content-Type": "application/json",
}


def _command(
    harness: ApiHarness,
    path: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    generation: int | None = None,
) -> Response:
    sent = dict(COMMAND_HEADERS)
    if generation is not None:
        sent["If-Match-Generation"] = str(generation)
    sent.update(headers or {})
    return harness.client.request("POST", path, headers=sent, body=json.dumps(payload).encode())


def _home_payload(role: str = "operator") -> dict[str, Any]:
    return {
        "api_version": "v1",
        "actor": {"user_id": OPERATOR.user_id, "role": role},
        "target": "both",
    }


def test_a_home_command_is_accepted_and_becomes_a_durable_operation(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/operations/home",
        _home_payload(),
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 202
    assert_conforms(response.body, "OperationAccepted")
    assert response.body["status_url"] == f"/v1/operations/{response.body['operation_id']}"
    recorded = harness.domain.operation(response.body["operation_id"])
    assert recorded is not None
    assert recorded.action is OperationAction.HOME


def test_a_replayed_key_returns_the_original_operation(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    generation = harness.domain.snapshot.generation

    first = _command(harness, "/v1/operations/home", _home_payload(), generation=generation)
    replayed = _command(harness, "/v1/operations/home", _home_payload(), generation=generation)

    assert first.status == replayed.status == 202
    assert first.body["operation_id"] == replayed.body["operation_id"]


def test_a_stale_generation_conflicts_without_admitting_anything(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = _command(harness, "/v1/operations/home", _home_payload(), generation=1)

    assert response.status == 409
    assert_conforms(response.body, "ConflictError")
    assert response.body["code"] == "STALE_GENERATION"


def test_a_key_reused_for_a_different_request_conflicts(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    generation = harness.domain.snapshot.generation
    _command(harness, "/v1/operations/home", _home_payload(), generation=generation)

    conflicting = _command(
        harness,
        "/v1/operations/home",
        {**_home_payload(), "target": "feeder"},
        generation=generation,
    )

    assert conflicting.status == 409
    assert conflicting.body["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.parametrize(
    ("header", "value", "code"),
    [
        ("Idempotency-Key", "too-short", "VALIDATION_FAILED"),
        ("Idempotency-Key", "key with spaces and enough length", "VALIDATION_FAILED"),
        ("If-Match-Generation", "*", "VALIDATION_FAILED"),
        ("If-Match-Generation", "latest", "VALIDATION_FAILED"),
        ("X-Deadline-Ms", "0", "DEADLINE_INVALID"),
        ("X-Deadline-Ms", "120001", "DEADLINE_INVALID"),
        ("X-Deadline-Ms", "forever", "DEADLINE_INVALID"),
    ],
)
def test_a_command_requires_well_formed_headers(
    make_api: Callable[..., ApiHarness],
    header: str,
    value: str,
    code: str,
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/operations/home",
        _home_payload(),
        headers={header: value},
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 400
    assert_conforms(response.body, "ValidationError")
    assert response.body["code"] == code


@pytest.mark.parametrize("header", ["Idempotency-Key", "If-Match-Generation", "X-Deadline-Ms"])
def test_a_command_requires_every_header(
    make_api: Callable[..., ApiHarness],
    header: str,
) -> None:
    harness = make_api()
    sent = {name: value for name, value in COMMAND_HEADERS.items() if name != header}

    response = harness.client.request(
        "POST",
        "/v1/operations/home",
        headers=sent,
        body=json.dumps(_home_payload()).encode(),
    )

    assert response.status == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"api_version": "v2", "actor": {"user_id": "u", "role": "operator"}, "target": "both"},
        {"api_version": "v1", "target": "both"},
        {"api_version": "v1", "actor": {"user_id": "u"}, "target": "both"},
        {"api_version": "v1", "actor": {"user_id": "u", "role": "root"}, "target": "both"},
        {"api_version": "v1", "actor": {"user_id": "u", "role": "operator"}},
        {
            "api_version": "v1",
            "actor": {"user_id": "u", "role": "operator"},
            "target": "both",
            "command": "sortto:3",
        },
    ],
)
def test_a_malformed_command_body_is_refused(
    make_api: Callable[..., ApiHarness],
    payload: dict[str, Any],
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/operations/home",
        payload,
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 400
    assert_conforms(response.body, "ValidationError")


def test_a_viewer_may_not_command_the_machine(make_api: Callable[..., ApiHarness]) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/operations/home",
        _home_payload(role="viewer"),
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 403
    assert_conforms(response.body, "ForbiddenError")


@pytest.mark.parametrize("slot", [-1, 64, "3", True])
def test_a_sort_outside_the_contract_slot_range_is_refused(
    make_api: Callable[..., ApiHarness],
    slot: Any,
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/operations/sort",
        {"api_version": "v1", "actor": {"user_id": "u", "role": "operator"}, "slot": slot},
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 400
    assert_conforms(response.body, "ValidationError")


def test_a_sort_before_homing_is_a_precondition_conflict(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/operations/sort",
        {"api_version": "v1", "actor": {"user_id": "u", "role": "operator"}, "slot": 3},
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 409
    assert_conforms(response.body, "ConflictError")
    assert response.body["code"] == "NOT_READY"


def test_feed_is_reported_as_unavailable_rather_than_attempted(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/operations/feed",
        {"api_version": "v1", "actor": {"user_id": "u", "role": "operator"}},
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 409
    assert_conforms(response.body, "ConflictError")
    assert response.body["code"] == "NOT_READY"
    assert "NOT_EXECUTED" in response.body["message"]


def test_a_priority_stop_is_accepted_against_a_stale_or_uncertain_view(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/machine/stop",
        {"api_version": "v1", "actor": {"user_id": "u", "role": "operator"}},
        headers={"If-Match-Generation": "*"},
    )

    assert response.status == 202
    assert_conforms(response.body, "OperationAccepted")
    recorded = harness.domain.operation(response.body["operation_id"])
    assert recorded is not None
    assert recorded.action is OperationAction.STOP


def test_a_stop_may_also_pin_an_exact_generation(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    stale = _command(
        harness,
        "/v1/machine/stop",
        {"api_version": "v1", "actor": {"user_id": "u", "role": "operator"}},
        generation=1,
    )

    assert stale.status == 409
    assert stale.body["code"] == "STALE_GENERATION"


def test_an_unknown_command_resource_is_not_found(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = _command(harness, "/v1/operations/dance", _home_payload())

    assert response.status == 404
    assert_conforms(response.body, "NotFoundError")


def _session_payload(path: str, role: str = "operator") -> dict[str, Any]:
    payload: dict[str, Any] = {
        "api_version": "v1",
        "actor": {"user_id": OPERATOR.user_id, "role": role},
    }
    if path.endswith("recover"):
        payload["confirm_uncertain_recovery"] = True
    return payload


@pytest.mark.parametrize("path", ["/v1/session/connect", "/v1/session/recover"])
def test_a_session_command_is_accepted_and_verifies_the_session(
    make_api: Callable[..., ApiHarness],
    path: str,
) -> None:
    harness = make_api()

    response = _command(
        harness,
        path,
        _session_payload(path),
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 202
    assert_conforms(response.body, "OperationAccepted")
    recorded = harness.terminals.await_terminal(response.body["operation_id"])
    assert recorded.state is OperationState.SUCCEEDED
    assert recorded.trusted_terminal
    assert recorded.outcome == "ready"
    assert harness.domain.snapshot.connection.name == "READY"
    expected = 1 if path.endswith("connect") else 2
    assert len(harness.simulators) == expected


def test_recovery_is_refused_without_an_explicit_confirmation(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/session/recover",
        {
            "api_version": "v1",
            "actor": {"user_id": OPERATOR.user_id, "role": "operator"},
            "confirm_uncertain_recovery": False,
        },
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 400
    assert_conforms(response.body, "ValidationError")
    assert "confirm_uncertain_recovery" in response.body["message"]


def test_recovery_replaces_the_session_rather_than_trusting_it(
    make_api: Callable[..., ApiHarness],
) -> None:
    harness = make_api()
    assert len(harness.simulators) == 1

    response = _command(
        harness,
        "/v1/session/recover",
        _session_payload("/v1/session/recover"),
        generation=harness.domain.snapshot.generation,
    )
    harness.terminals.await_terminal(response.body["operation_id"])

    # A fresh transport is opened rather than the existing one reused.
    assert len(harness.simulators) == 2
    assert harness.simulators[0].closed


def test_a_viewer_may_not_command_the_session(make_api: Callable[..., ApiHarness]) -> None:
    harness = make_api()

    response = _command(
        harness,
        "/v1/session/connect",
        _session_payload("/v1/session/connect", role="viewer"),
        generation=harness.domain.snapshot.generation,
    )

    assert response.status == 403
    assert_conforms(response.body, "ForbiddenError")

from __future__ import annotations

import http.client
import json
import shutil
import socket
import tempfile
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cs71vision.api import Trainable, VisionApiServer
from cs71vision.dataset import IN_MEMORY, DatasetStore, TrainedCandidate

TOKEN = "cs71-vision-test-credential"  # noqa: S105 - test fixture, not a secret
START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


class _UnixHTTPConnection(http.client.HTTPConnection):
    """Mirrors `cs71vision.daemon_client._UnixHTTPConnection` for tests."""

    def __init__(self, socket_path: str, *, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self._socket_path)
        self.sock = connection


def _get(
    socket_path: str, path: str, *, token: str | None = TOKEN
) -> tuple[int, dict[str, object]]:
    return _request(socket_path, "GET", path, token=token)


def _post(
    socket_path: str, path: str, *, token: str | None = TOKEN
) -> tuple[int, dict[str, object]]:
    return _request(socket_path, "POST", path, token=token)


def _request(
    socket_path: str, method: str, path: str, *, token: str | None
) -> tuple[int, dict[str, object]]:
    connection = _UnixHTTPConnection(socket_path)
    try:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        payload = response.read()
    finally:
        connection.close()
    return response.status, json.loads(payload)


@pytest.fixture
def workspace() -> Iterator[Path]:
    directory = Path(tempfile.mkdtemp(prefix="cs71vision-api"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _store_with(counts: dict[int, int]) -> DatasetStore:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    index = 0
    for slot, count in counts.items():
        for _ in range(count):
            store.record_example(
                operation_id=f"op-{index}",
                slot=slot,
                frame_png=b"x",
                frame_captured_at=START,
                operation_created_at=START,
            )
            index += 1
    return store


def _candidate(**overrides: object) -> TrainedCandidate:
    defaults: dict[str, object] = {
        "model_blob": b"fake-model-bytes",
        "included_classes": (3, 5),
        "excluded_classes": (7,),
        "accuracy_by_class": {3: 1.0, 5: 0.9},
        "minimum_examples_per_class": 40,
        "training_example_count": 64,
        "holdout_example_count": 16,
    }
    defaults.update(overrides)
    return TrainedCandidate(**defaults)  # type: ignore[arg-type]


class FakeTrainingJob:
    """A `Trainable` the test can inspect and script the answer of."""

    def __init__(self, *, started: bool = True) -> None:
        self.trigger_calls = 0
        self.closed = False
        self._started = started

    def trigger(self) -> bool:
        self.trigger_calls += 1
        return self._started

    def close(self) -> None:
        self.closed = True


def _server(
    workspace: Path,
    *,
    store: DatasetStore | None = None,
    socket_path: str | None = None,
    service_token: str = TOKEN,
    minimum_examples_per_class: int = 40,
    training_job: Trainable | None = None,
    now: Callable[[], datetime] = lambda: START,
) -> VisionApiServer:
    return VisionApiServer(
        store if store is not None else _store_with({}),
        socket_path=socket_path if socket_path is not None else str(workspace / "cs71vision.sock"),
        service_token=service_token,
        minimum_examples_per_class=minimum_examples_per_class,
        training_job=training_job if training_job is not None else FakeTrainingJob(),
        now=now,
    )


def test_construction_requires_a_service_token(workspace: Path) -> None:
    with pytest.raises(ValueError, match="service token"):
        _server(workspace, service_token="")


def test_construction_requires_a_positive_floor(workspace: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        _server(workspace, minimum_examples_per_class=0)


def test_dataset_reports_per_class_counts_against_the_floor(workspace: Path) -> None:
    server = _server(workspace, store=_store_with({3: 5, 5: 2}), minimum_examples_per_class=4)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/dataset")

        assert status == 200
        assert body["api_version"] == "v1"
        assert body["minimum_examples_per_class"] == 4
        assert body["classes"] == [
            {"slot": 3, "count": 5, "eligible": True},
            {"slot": 5, "count": 2, "eligible": False},
        ]
        assert body["training_ready"] is True
    finally:
        server.close()


def test_dataset_is_not_training_ready_when_every_class_is_below_the_floor(
    workspace: Path,
) -> None:
    server = _server(workspace, store=_store_with({3: 1}))
    server.start()
    try:
        _, body = _get(server.socket_path, "/v1/dataset")

        assert body["classes"] == [{"slot": 3, "count": 1, "eligible": False}]
        assert body["training_ready"] is False
    finally:
        server.close()


def test_dataset_with_no_examples_yet_is_an_empty_list_not_an_error(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/dataset")

        assert status == 200
        assert body["classes"] == []
        assert body["training_ready"] is False
    finally:
        server.close()


def test_a_missing_credential_is_refused(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/dataset", token=None)

        assert status == 401
        assert body["code"] == "UNAUTHENTICATED"
    finally:
        server.close()


def test_a_wrong_credential_is_refused(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, _ = _get(server.socket_path, "/v1/dataset", token="wrong-token")

        assert status == 401
    finally:
        server.close()


def test_an_unknown_get_path_is_not_found(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/unknown")

        assert status == 404
        assert body["code"] == "RESOURCE_NOT_FOUND"
    finally:
        server.close()


def test_an_unknown_post_path_is_not_found(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/unknown")

        assert status == 404
        assert body["code"] == "RESOURCE_NOT_FOUND"
    finally:
        server.close()


def test_starting_twice_is_a_noop(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        server.start()  # a second bind attempt here would raise
        status, _ = _get(server.socket_path, "/v1/dataset")
        assert status == 200
    finally:
        server.close()


def test_close_releases_the_dataset_store(workspace: Path) -> None:
    store = _store_with({})
    server = _server(workspace, store=store)
    server.start()

    server.close()

    with pytest.raises(Exception, match="closed"):
        store.total_examples()


def test_close_also_closes_the_training_job(workspace: Path) -> None:
    trainer = FakeTrainingJob()
    server = _server(workspace, training_job=trainer)
    server.start()

    server.close()

    assert trainer.closed is True


def test_a_stale_socket_file_is_replaced_when_nothing_is_serving_it(workspace: Path) -> None:
    socket_path = workspace / "cs71vision.sock"
    socket_path.touch()

    server = _server(workspace, socket_path=str(socket_path))
    server.start()
    try:
        status, _ = _get(server.socket_path, "/v1/dataset")
        assert status == 200
    finally:
        server.close()


def test_refuses_to_steal_a_socket_another_server_is_already_serving(workspace: Path) -> None:
    socket_path = str(workspace / "cs71vision.sock")
    first = _server(workspace, socket_path=socket_path)
    first.start()
    try:
        second = _server(workspace, socket_path=socket_path)
        with pytest.raises(OSError, match="already serving"):
            second.start()
    finally:
        first.close()


def test_models_reports_every_candidate_and_the_active_version(workspace: Path) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    store.activate(version, activated_at=START)
    server = _server(workspace, store=store)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/models")

        assert status == 200
        assert body["api_version"] == "v1"
        assert body["active_version"] == version
        assert body["can_roll_back"] is False  # only one activation ever happened
        candidates = body["candidates"]
        assert isinstance(candidates, list)
        [candidate] = candidates
        assert candidate["version"] == version
        assert candidate["included_classes"] == [3, 5]
        assert candidate["excluded_classes"] == [7]
        assert candidate["accuracy_by_class"] == {"3": 1.0, "5": 0.9}
    finally:
        server.close()


def test_models_active_version_is_none_before_anything_is_activated(workspace: Path) -> None:
    store = _store_with({})
    store.record_candidate(_candidate(), trained_at=START)
    server = _server(workspace, store=store)
    server.start()
    try:
        _, body = _get(server.socket_path, "/v1/models")

        assert body["active_version"] is None
        assert body["can_roll_back"] is False
    finally:
        server.close()


def test_models_can_roll_back_once_a_second_activation_exists(workspace: Path) -> None:
    store = _store_with({})
    first = store.record_candidate(_candidate(model_blob=b"v1"), trained_at=START)
    second = store.record_candidate(_candidate(model_blob=b"v2"), trained_at=START)
    store.activate(first, activated_at=START)
    store.activate(second, activated_at=START)
    server = _server(workspace, store=store)
    server.start()
    try:
        _, body = _get(server.socket_path, "/v1/models")

        assert body["can_roll_back"] is True
    finally:
        server.close()


def test_train_triggers_the_training_job(workspace: Path) -> None:
    trainer = FakeTrainingJob(started=True)
    server = _server(workspace, training_job=trainer)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/train")

        assert status == 200
        assert body == {"api_version": "v1", "started": True}
        assert trainer.trigger_calls == 1
    finally:
        server.close()


def test_train_reports_started_false_when_a_run_is_already_in_flight(workspace: Path) -> None:
    trainer = FakeTrainingJob(started=False)
    server = _server(workspace, training_job=trainer)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/train")

        assert status == 200
        assert body == {"api_version": "v1", "started": False}
    finally:
        server.close()


def test_activate_makes_a_candidate_the_active_version(workspace: Path) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    server = _server(workspace, store=store, now=lambda: START)
    server.start()
    try:
        status, body = _post(server.socket_path, f"/v1/models/{version}/activate")

        assert status == 200
        assert body == {
            "api_version": "v1",
            "active_version": version,
            "activated_at": START.isoformat(),
        }
        assert store.active_version() == version
    finally:
        server.close()


def test_activating_an_unknown_version_is_not_found(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/models/999/activate")

        assert status == 404
        assert body["code"] == "RESOURCE_NOT_FOUND"
    finally:
        server.close()


def test_rollback_activates_the_previously_active_version(workspace: Path) -> None:
    store = _store_with({})
    first = store.record_candidate(_candidate(model_blob=b"v1"), trained_at=START)
    second = store.record_candidate(_candidate(model_blob=b"v2"), trained_at=START)
    store.activate(first, activated_at=START)
    store.activate(second, activated_at=START)
    server = _server(workspace, store=store, now=lambda: START)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/rollback")

        assert status == 200
        assert body["active_version"] == first
        assert store.active_version() == first
    finally:
        server.close()


def test_rollback_with_no_prior_activation_is_refused(workspace: Path) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    store.activate(version, activated_at=START)
    server = _server(workspace, store=store)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/rollback")

        assert status == 400
        assert body["code"] == "VALIDATION_FAILED"
    finally:
        server.close()


def test_rollback_with_nothing_ever_activated_is_refused(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/rollback")

        assert status == 400
        assert body["code"] == "VALIDATION_FAILED"
    finally:
        server.close()

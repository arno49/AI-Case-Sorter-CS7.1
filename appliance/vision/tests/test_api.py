from __future__ import annotations

import http.client
import json
import shutil
import socket
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cs71vision.api import DatasetApiServer
from cs71vision.dataset import IN_MEMORY, DatasetStore

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
    connection = _UnixHTTPConnection(socket_path)
    try:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        connection.request("GET", path, headers=headers)
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


def test_construction_requires_a_service_token() -> None:
    with pytest.raises(ValueError, match="service token"):
        DatasetApiServer(
            _store_with({}),
            socket_path="/tmp/whatever.sock",
            service_token="",
            minimum_examples_per_class=1,
        )


def test_construction_requires_a_positive_floor() -> None:
    with pytest.raises(ValueError, match="positive"):
        DatasetApiServer(
            _store_with({}),
            socket_path="/tmp/whatever.sock",
            service_token=TOKEN,
            minimum_examples_per_class=0,
        )


def test_dataset_reports_per_class_counts_against_the_floor(workspace: Path) -> None:
    server = DatasetApiServer(
        _store_with({3: 5, 5: 2}),
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=4,
    )
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
    server = DatasetApiServer(
        _store_with({3: 1}),
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()
    try:
        _, body = _get(server.socket_path, "/v1/dataset")

        assert body["classes"] == [{"slot": 3, "count": 1, "eligible": False}]
        assert body["training_ready"] is False
    finally:
        server.close()


def test_dataset_with_no_examples_yet_is_an_empty_list_not_an_error(workspace: Path) -> None:
    server = DatasetApiServer(
        _store_with({}),
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/dataset")

        assert status == 200
        assert body["classes"] == []
        assert body["training_ready"] is False
    finally:
        server.close()


def test_a_missing_credential_is_refused(workspace: Path) -> None:
    server = DatasetApiServer(
        _store_with({}),
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/dataset", token=None)

        assert status == 401
        assert body["code"] == "UNAUTHENTICATED"
    finally:
        server.close()


def test_a_wrong_credential_is_refused(workspace: Path) -> None:
    server = DatasetApiServer(
        _store_with({}),
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()
    try:
        status, _ = _get(server.socket_path, "/v1/dataset", token="wrong-token")

        assert status == 401
    finally:
        server.close()


def test_an_unknown_path_is_not_found(workspace: Path) -> None:
    server = DatasetApiServer(
        _store_with({}),
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/unknown")

        assert status == 404
        assert body["code"] == "RESOURCE_NOT_FOUND"
    finally:
        server.close()


def test_starting_twice_is_a_noop(workspace: Path) -> None:
    server = DatasetApiServer(
        _store_with({}),
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()
    try:
        server.start()  # a second bind attempt here would raise
        status, _ = _get(server.socket_path, "/v1/dataset")
        assert status == 200
    finally:
        server.close()


def test_close_releases_the_dataset_store(workspace: Path) -> None:
    store = _store_with({})
    server = DatasetApiServer(
        store,
        socket_path=str(workspace / "cs71vision.sock"),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()

    server.close()

    with pytest.raises(Exception, match="closed"):
        store.total_examples()


def test_a_stale_socket_file_is_replaced_when_nothing_is_serving_it(workspace: Path) -> None:
    socket_path = workspace / "cs71vision.sock"
    socket_path.touch()

    server = DatasetApiServer(
        _store_with({}),
        socket_path=str(socket_path),
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    server.start()
    try:
        status, _ = _get(server.socket_path, "/v1/dataset")
        assert status == 200
    finally:
        server.close()


def test_refuses_to_steal_a_socket_another_server_is_already_serving(workspace: Path) -> None:
    socket_path = str(workspace / "cs71vision.sock")
    first = DatasetApiServer(
        _store_with({}),
        socket_path=socket_path,
        service_token=TOKEN,
        minimum_examples_per_class=40,
    )
    first.start()
    try:
        second = DatasetApiServer(
            _store_with({}),
            socket_path=socket_path,
            service_token=TOKEN,
            minimum_examples_per_class=40,
        )
        with pytest.raises(OSError, match="already serving"):
            second.start()
    finally:
        first.close()

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
from cs71vision.routing import RoutingSession

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
    socket_path: str,
    path: str,
    *,
    token: str | None = TOKEN,
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    return _request(socket_path, "POST", path, token=token, body=body)


def _request(
    socket_path: str,
    method: str,
    path: str,
    *,
    token: str | None,
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    connection = _UnixHTTPConnection(socket_path)
    try:
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        encoded = None if body is None else json.dumps(body).encode("utf-8")
        connection.request(method, path, body=encoded, headers=headers)
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
    autonomy_thresholds: dict[int, float] | None = None,
    routing: RoutingSession | None = None,
    now: Callable[[], datetime] = lambda: START,
) -> VisionApiServer:
    return VisionApiServer(
        store if store is not None else _store_with({}),
        socket_path=socket_path if socket_path is not None else str(workspace / "cs71vision.sock"),
        service_token=service_token,
        minimum_examples_per_class=minimum_examples_per_class,
        training_job=training_job if training_job is not None else FakeTrainingJob(),
        autonomy_thresholds=autonomy_thresholds if autonomy_thresholds is not None else {},
        routing=routing,
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


def test_suggestion_is_none_before_anything_is_recorded(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/suggestion")

        assert status == 200
        assert body["api_version"] == "v1"
        assert body["suggestion"] is None
    finally:
        server.close()


def test_suggestion_reports_the_most_recently_recorded_one(workspace: Path) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.87,
        frame_captured_at=START,
        suggested_at=START,
    )
    server = _server(workspace, store=store)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/suggestion")

        assert status == 200
        assert body["suggestion"] == {
            "slot": 3,
            "confidence": 0.87,
            "model_version": version,
            "suggested_at": START.isoformat(),
            "primer_present": None,
            "requires_confirmation": True,
        }
    finally:
        server.close()


def test_suggestion_reports_requires_confirmation_false_only_for_a_confidently_clear_primer_axis(
    workspace: Path,
) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.87,
        frame_captured_at=START,
        suggested_at=START,
        primer_present=False,
    )
    server = _server(workspace, store=store)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/suggestion")

        assert status == 200
        assert body["suggestion"] == {
            "slot": 3,
            "confidence": 0.87,
            "model_version": version,
            "suggested_at": START.isoformat(),
            "primer_present": False,
            "requires_confirmation": False,
        }
    finally:
        server.close()


def test_suggestion_reports_requires_confirmation_true_when_primer_is_flagged(
    workspace: Path,
) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.87,
        frame_captured_at=START,
        suggested_at=START,
        primer_present=True,
    )
    server = _server(workspace, store=store)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/suggestion")

        assert status == 200
        assert body["suggestion"] == {
            "slot": 3,
            "confidence": 0.87,
            "model_version": version,
            "suggested_at": START.isoformat(),
            "primer_present": True,
            "requires_confirmation": True,
        }
    finally:
        server.close()


def test_suggestion_accuracy_is_zero_before_anything_is_matched(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/suggestion-accuracy")

        assert status == 200
        assert body == {"api_version": "v1", "total": 0, "correct": 0, "accuracy": None}
    finally:
        server.close()


def test_suggestion_accuracy_reports_matched_outcomes(workspace: Path) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    suggestion_id = store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.9,
        frame_captured_at=START,
        suggested_at=START,
    )
    store.record_suggestion_outcome(
        suggestion_id=suggestion_id, operation_id="op-1", actual_slot=3, recorded_at=START
    )
    server = _server(workspace, store=store)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/suggestion-accuracy")

        assert status == 200
        assert body == {"api_version": "v1", "total": 1, "correct": 1, "accuracy": 1.0}
    finally:
        server.close()


def test_autonomy_reports_configured_thresholds_with_no_activity_yet(workspace: Path) -> None:
    server = _server(workspace, autonomy_thresholds={3: 0.95, 5: 0.9})
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/autonomy")

        assert status == 200
        assert body == {
            "api_version": "v1",
            "thresholds": {"3": 0.95, "5": 0.9},
            "accuracy_by_class": {},
            "pending_review": [],
        }
    finally:
        server.close()


def test_autonomy_lists_a_pending_review_and_excludes_it_from_accuracy(
    workspace: Path,
) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    suggestion_id = store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.97,
        primer_present=False,
        frame_captured_at=START,
        suggested_at=START,
    )
    attempt_id = store.record_autonomous_attempt(
        suggestion_id=suggestion_id, operation_id="op-1", slot=3, attempted_at=START
    )
    server = _server(workspace, store=store, autonomy_thresholds={3: 0.95})
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/autonomy")

        assert status == 200
        assert body["accuracy_by_class"] == {}
        assert body["pending_review"] == [
            {
                "attempt_id": attempt_id,
                "suggestion_id": suggestion_id,
                "operation_id": "op-1",
                "slot": 3,
                "attempted_at": START.isoformat(),
            }
        ]
    finally:
        server.close()


def test_autonomy_reports_the_false_rate_for_a_reviewed_class(workspace: Path) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    suggestion_id = store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.97,
        primer_present=False,
        frame_captured_at=START,
        suggested_at=START,
    )
    attempt_id = store.record_autonomous_attempt(
        suggestion_id=suggestion_id, operation_id="op-1", slot=3, attempted_at=START
    )
    store.record_autonomous_review(attempt_id=attempt_id, correct=False, reviewed_at=START)
    server = _server(workspace, store=store, autonomy_thresholds={3: 0.95})
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/autonomy")

        assert status == 200
        assert body["accuracy_by_class"] == {"3": {"total": 1, "correct": 0, "false_rate": 1.0}}
        assert body["pending_review"] == []
    finally:
        server.close()


def test_autonomous_review_records_a_verdict(workspace: Path) -> None:
    store = _store_with({})
    version = store.record_candidate(_candidate(), trained_at=START)
    suggestion_id = store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.97,
        primer_present=False,
        frame_captured_at=START,
        suggested_at=START,
    )
    attempt_id = store.record_autonomous_attempt(
        suggestion_id=suggestion_id, operation_id="op-1", slot=3, attempted_at=START
    )
    server = _server(workspace, store=store)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/autonomous-reviews",
            body={"api_version": "v1", "attempt_id": attempt_id, "correct": True},
        )

        assert status == 200
        assert body["attempt_id"] == attempt_id
        assert body["correct"] is True

        follow_up = _get(server.socket_path, "/v1/autonomy")[1]
        assert follow_up["pending_review"] == []
    finally:
        server.close()


def test_autonomous_review_for_an_unknown_attempt_is_not_found(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/autonomous-reviews",
            body={"api_version": "v1", "attempt_id": 999, "correct": True},
        )

        assert status == 404
        assert body["code"] == "RESOURCE_NOT_FOUND"
    finally:
        server.close()


def test_autonomous_review_refuses_extra_fields(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/autonomous-reviews",
            body={"api_version": "v1", "attempt_id": 1, "correct": True, "note": "looks fine"},
        )

        assert status == 400
        assert body["code"] == "VALIDATION_FAILED"
    finally:
        server.close()


def test_autonomous_review_refuses_a_missing_body(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/autonomous-reviews")

        assert status == 400
        assert body["code"] == "VALIDATION_FAILED"
    finally:
        server.close()


def test_routing_reports_inactive_before_any_run_starts(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _get(server.socket_path, "/v1/routing")

        assert status == 200
        assert body == {
            "api_version": "v1",
            "active": False,
            "kind": None,
            "started_at": None,
            "source_group": None,
            "legend": [],
        }
    finally:
        server.close()


def test_routing_start_fixed_reports_the_map_and_one_overflow_entry(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={
                "api_version": "v1",
                "kind": "fixed",
                "class_to_slot": {"12": 3, "45": 5},
                "overflow_slot": 7,
            },
        )

        assert status == 200
        assert body["active"] is True
        assert body["kind"] == "fixed"
        assert body["source_group"] is None
        # _legend_for already sorts by slot - fixed/two-pass legends are
        # deterministic, not just "eventually consistent" in some order.
        assert body["legend"] == [
            {"slot": 3, "class_id": 12, "overflow": False},
            {"slot": 5, "class_id": 45, "overflow": False},
            {"slot": 7, "class_id": None, "overflow": True},
        ]
    finally:
        server.close()


def test_routing_start_fixed_refuses_an_overflow_slot_that_is_also_mapped(
    workspace: Path,
) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={
                "api_version": "v1",
                "kind": "fixed",
                "class_to_slot": {"12": 3},
                "overflow_slot": 3,
            },
        )

        assert status == 400
        assert body["code"] == "VALIDATION_FAILED"
    finally:
        server.close()


def test_routing_start_dynamic_reports_kind_and_no_claims_yet(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={"api_version": "v1", "kind": "dynamic", "available_slots": [1, 2, 3]},
        )

        assert status == 200
        assert body["kind"] == "dynamic"
        assert body["legend"] == []
    finally:
        server.close()


def test_routing_start_two_pass_reports_its_source_group(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={
                "api_version": "v1",
                "kind": "two_pass",
                "class_to_slot": {"12": 3},
                "overflow_slot": 7,
                "source_group": 9,
            },
        )

        assert status == 200
        assert body["kind"] == "two_pass"
        assert body["source_group"] == 9
    finally:
        server.close()


def test_routing_start_two_pass_source_group_is_optional(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={
                "api_version": "v1",
                "kind": "two_pass",
                "class_to_slot": {"12": 3},
                "overflow_slot": 7,
            },
        )

        assert status == 200
        assert body["source_group"] is None
    finally:
        server.close()


def test_routing_start_refuses_an_unknown_kind(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={"api_version": "v1", "kind": "unknown"},
        )

        assert status == 400
        assert body["code"] == "VALIDATION_FAILED"
    finally:
        server.close()


def test_routing_start_refuses_extra_fields(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={
                "api_version": "v1",
                "kind": "dynamic",
                "available_slots": [1],
                "note": "extra",
            },
        )

        assert status == 400
        assert body["code"] == "VALIDATION_FAILED"
    finally:
        server.close()


def test_routing_stop_deactivates_the_run(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        _post(
            server.socket_path,
            "/v1/routing/start",
            body={"api_version": "v1", "kind": "dynamic", "available_slots": [1]},
        )

        status, body = _post(server.socket_path, "/v1/routing/stop")

        assert status == 200
        assert body["active"] is False
        assert _get(server.socket_path, "/v1/routing")[1]["active"] is False
    finally:
        server.close()


def test_routing_stop_without_an_active_run_is_a_noop_not_an_error(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        status, body = _post(server.socket_path, "/v1/routing/stop")

        assert status == 200
        assert body["active"] is False
    finally:
        server.close()


def test_routing_start_replaces_the_previous_run_entirely(workspace: Path) -> None:
    server = _server(workspace)
    server.start()
    try:
        _post(
            server.socket_path,
            "/v1/routing/start",
            body={"api_version": "v1", "kind": "dynamic", "available_slots": [1]},
        )

        status, body = _post(
            server.socket_path,
            "/v1/routing/start",
            body={
                "api_version": "v1",
                "kind": "fixed",
                "class_to_slot": {"5": 2},
                "overflow_slot": 9,
            },
        )

        assert status == 200
        assert body["kind"] == "fixed"
    finally:
        server.close()

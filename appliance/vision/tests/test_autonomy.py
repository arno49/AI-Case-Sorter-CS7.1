from __future__ import annotations

import shutil
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cs71vision.autonomy import Autonomist, may_autonomously_sort
from cs71vision.classifier import Suggestion
from cs71vision.daemon_client import DaemonClientError
from cs71vision.dataset import DatasetStore, TrainedCandidate

START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def workspace() -> Iterator[Path]:
    directory = Path(tempfile.mkdtemp(prefix="cs71vision-autonomy"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _candidate() -> TrainedCandidate:
    return TrainedCandidate(
        model_blob=b"fake-model-bytes",
        included_classes=(3,),
        excluded_classes=(),
        accuracy_by_class={3: 1.0},
        minimum_examples_per_class=40,
        training_example_count=64,
        holdout_example_count=16,
    )


class FakeSortSubmitter:
    """A `SortSubmitter` double that records what it was asked to submit."""

    def __init__(self, *, operation_id: str = "op-1", generation: int = 41) -> None:
        self.operation_id = operation_id
        self.generation = generation
        self.calls: list[dict[str, object]] = []
        self.raises: Exception | None = None

    def current_generation(self) -> int:
        return self.generation

    def submit_sort(self, *, slot: int, generation: int, idempotency_key: str) -> str:
        if self.raises is not None:
            raise self.raises
        self.calls.append(
            {"slot": slot, "generation": generation, "idempotency_key": idempotency_key}
        )
        return self.operation_id


@pytest.mark.parametrize(
    ("primer_present", "confidence", "threshold", "expected"),
    [
        (False, 0.97, 0.95, True),
        (False, 0.94, 0.95, False),  # below threshold
        (False, 0.97, None, False),  # class absent from thresholds
        (True, 0.99, 0.5, False),  # primer flagged overrides any confidence
        (None, 0.99, 0.5, False),  # primer unknown overrides any confidence
    ],
)
def test_may_autonomously_sort_composes_primer_and_confidence_gates(
    primer_present: bool | None, confidence: float, threshold: float | None, expected: bool
) -> None:
    suggestion = Suggestion(slot=3, confidence=confidence, primer_present=primer_present)
    thresholds = {} if threshold is None else {3: threshold}

    assert may_autonomously_sort(suggestion, thresholds) is expected


def test_may_autonomously_sort_never_lets_a_high_threshold_on_one_class_leak_to_another() -> None:
    suggestion = Suggestion(slot=5, confidence=0.99, primer_present=False)

    assert may_autonomously_sort(suggestion, {3: 0.5}) is False


def test_attempt_once_does_nothing_without_a_suggestion(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        client = FakeSortSubmitter()
        autonomist = Autonomist(store, client, {3: 0.5}, now=lambda: START)

        assert autonomist.attempt_once() == 0
        assert client.calls == []
    finally:
        store.close()


def test_attempt_once_submits_a_clear_above_threshold_suggestion(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        suggestion_id = store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.97,
            primer_present=False,
            frame_captured_at=START,
            suggested_at=START,
        )
        client = FakeSortSubmitter()
        autonomist = Autonomist(store, client, {3: 0.95}, now=lambda: START)

        result = autonomist.attempt_once()

        assert result == 1
        assert len(client.calls) == 1
        assert client.calls[0]["slot"] == 3
        assert client.calls[0]["generation"] == 41
        assert store.autonomous_attempt_exists(suggestion_id) is True
    finally:
        store.close()


def test_attempt_once_never_submits_a_below_threshold_suggestion(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.80,
            primer_present=False,
            frame_captured_at=START,
            suggested_at=START,
        )
        client = FakeSortSubmitter()
        autonomist = Autonomist(store, client, {3: 0.95}, now=lambda: START)

        assert autonomist.attempt_once() == 0
        assert client.calls == []
    finally:
        store.close()


def test_attempt_once_never_submits_when_primer_is_not_confidently_clear(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.99,
            primer_present=None,
            frame_captured_at=START,
            suggested_at=START,
        )
        client = FakeSortSubmitter()
        # An arbitrarily low threshold must not matter: primer decides first.
        autonomist = Autonomist(store, client, {3: 0.0}, now=lambda: START)

        assert autonomist.attempt_once() == 0
        assert client.calls == []
    finally:
        store.close()


def test_attempt_once_never_submits_twice_for_the_same_suggestion(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.97,
            primer_present=False,
            frame_captured_at=START,
            suggested_at=START,
        )
        client = FakeSortSubmitter()
        autonomist = Autonomist(store, client, {3: 0.95}, now=lambda: START)

        assert autonomist.attempt_once() == 1
        assert autonomist.attempt_once() == 0
        assert len(client.calls) == 1
    finally:
        store.close()


def test_attempt_once_records_nothing_when_cs71d_refuses_the_submission(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        suggestion_id = store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.97,
            primer_present=False,
            frame_captured_at=START,
            suggested_at=START,
        )
        client = FakeSortSubmitter()
        client.raises = DaemonClientError("HTTP 409: STALE_GENERATION")
        autonomist = Autonomist(store, client, {3: 0.95}, now=lambda: START)

        assert autonomist.attempt_once() == 0
        assert store.autonomous_attempt_exists(suggestion_id) is False
    finally:
        store.close()

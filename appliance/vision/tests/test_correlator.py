from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cs71vision.camera import Frame
from cs71vision.correlator import Correlator, FrameBuffer
from cs71vision.daemon_client import DaemonClientError, SortSuccess
from cs71vision.dataset import IN_MEMORY, DatasetStore, TrainedCandidate

START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _frame(*, seconds: int) -> Frame:
    return Frame(
        png=f"frame-{seconds}".encode(),
        width=1,
        height=1,
        captured_at=START + timedelta(seconds=seconds),
    )


class FakeDaemonClient:
    def __init__(self, successes: tuple[SortSuccess, ...] = ()) -> None:
        self.successes = successes
        self.calls = 0

    def recent_sort_successes(self, *, limit: int = 25) -> tuple[SortSuccess, ...]:
        self.calls += 1
        return self.successes


class FailingDaemonClient:
    def recent_sort_successes(self, *, limit: int = 25) -> tuple[SortSuccess, ...]:
        raise DaemonClientError("cs71d is unreachable")


def test_frame_buffer_finds_the_closest_frame_at_or_before_a_moment() -> None:
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=0))
    buffer.add(_frame(seconds=5))
    buffer.add(_frame(seconds=10))

    found = buffer.closest_at_or_before(START + timedelta(seconds=7))

    assert found is not None
    assert found.captured_at == START + timedelta(seconds=5)


def test_frame_buffer_returns_none_when_nothing_qualifies() -> None:
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=10))

    assert buffer.closest_at_or_before(START) is None


def test_frame_buffer_returns_none_when_empty() -> None:
    buffer = FrameBuffer()

    assert buffer.closest_at_or_before(START) is None


def test_frame_buffer_evicts_the_oldest_frame_past_capacity() -> None:
    buffer = FrameBuffer(capacity=2)
    buffer.add(_frame(seconds=0))
    buffer.add(_frame(seconds=5))
    buffer.add(_frame(seconds=10))

    # seconds=0 was evicted; the closest frame at-or-before START is now none.
    assert buffer.closest_at_or_before(START) is None
    assert buffer.closest_at_or_before(START + timedelta(seconds=5)) is not None


def test_frame_buffer_refuses_a_nonpositive_capacity() -> None:
    with pytest.raises(ValueError, match="positive"):
        FrameBuffer(capacity=0)


def test_poll_once_records_an_example_when_a_frame_is_available() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=0))
    client = FakeDaemonClient(
        (
            SortSuccess(
                operation_id="op-1", slot=3, created_at=(START + timedelta(seconds=1)).isoformat()
            ),
        )
    )
    correlator = Correlator(client, store, buffer)

    recorded = correlator.poll_once()

    assert recorded == 1
    assert store.has_example("op-1")
    assert store.counts_by_slot() == {3: 1}


def test_poll_once_discards_a_success_with_no_matching_frame() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    buffer = FrameBuffer()  # empty: nothing was ever captured
    client = FakeDaemonClient(
        (SortSuccess(operation_id="op-1", slot=3, created_at=START.isoformat()),)
    )
    correlator = Correlator(client, store, buffer)

    recorded = correlator.poll_once()

    assert recorded == 0
    assert not store.has_example("op-1")
    assert store.total_examples() == 0


def test_poll_once_is_idempotent_across_repeated_polls() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=0))
    client = FakeDaemonClient(
        (
            SortSuccess(
                operation_id="op-1", slot=3, created_at=(START + timedelta(seconds=1)).isoformat()
            ),
        )
    )
    correlator = Correlator(client, store, buffer)

    first = correlator.poll_once()
    second = correlator.poll_once()

    assert first == 1
    assert second == 0
    assert store.total_examples() == 1


def test_poll_once_discards_a_success_with_an_unparseable_timestamp() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=0))
    client = FakeDaemonClient(
        (SortSuccess(operation_id="op-1", slot=3, created_at="not-a-timestamp"),)
    )
    correlator = Correlator(client, store, buffer)

    assert correlator.poll_once() == 0
    assert store.total_examples() == 0


def test_poll_once_survives_an_unreachable_daemon() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    correlator = Correlator(FailingDaemonClient(), store, FrameBuffer())

    assert correlator.poll_once() == 0


def _candidate() -> TrainedCandidate:
    return TrainedCandidate(
        model_blob=b"model",
        included_classes=(3, 5),
        excluded_classes=(),
        accuracy_by_class={3: 1.0, 5: 1.0},
        minimum_examples_per_class=5,
        training_example_count=10,
        holdout_example_count=2,
    )


def test_poll_once_matches_a_newly_recorded_example_to_its_live_suggestion() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    version = store.record_candidate(_candidate(), trained_at=START)
    suggestion_id = store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.8,
        frame_captured_at=START,
        suggested_at=START,
    )
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=0))
    client = FakeDaemonClient(
        (
            SortSuccess(
                operation_id="op-1", slot=3, created_at=(START + timedelta(seconds=1)).isoformat()
            ),
        )
    )
    correlator = Correlator(client, store, buffer)

    correlator.poll_once()

    accuracy = store.suggestion_accuracy()
    assert accuracy.total == 1
    assert accuracy.correct == 1
    assert store.unmatched_suggestion_at_or_before(START + timedelta(minutes=1)) is None
    assert suggestion_id is not None  # sanity: the id above was real


def test_poll_once_records_an_incorrect_outcome_when_the_operator_chose_differently() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    version = store.record_candidate(_candidate(), trained_at=START)
    store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.8,
        frame_captured_at=START,
        suggested_at=START,
    )
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=0))
    client = FakeDaemonClient(
        (
            SortSuccess(
                operation_id="op-1", slot=5, created_at=(START + timedelta(seconds=1)).isoformat()
            ),
        )
    )
    correlator = Correlator(client, store, buffer)

    correlator.poll_once()

    accuracy = store.suggestion_accuracy()
    assert accuracy.total == 1
    assert accuracy.correct == 0


def test_poll_once_leaves_suggestion_accuracy_untouched_with_no_suggestion_to_match() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    buffer = FrameBuffer()
    buffer.add(_frame(seconds=0))
    client = FakeDaemonClient(
        (
            SortSuccess(
                operation_id="op-1", slot=3, created_at=(START + timedelta(seconds=1)).isoformat()
            ),
        )
    )
    correlator = Correlator(client, store, buffer)

    correlator.poll_once()

    assert store.suggestion_accuracy().total == 0

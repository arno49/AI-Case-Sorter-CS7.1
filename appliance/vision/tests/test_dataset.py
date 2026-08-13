from __future__ import annotations

import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cs71vision.dataset import (
    IN_MEMORY,
    DatasetError,
    DatasetExample,
    DatasetSchemaError,
    DatasetStore,
    TrainedCandidate,
)

START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def workspace() -> Iterator[Path]:
    directory = Path(tempfile.mkdtemp(prefix="cs71vision-dataset"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def test_opening_creates_a_wal_mode_owner_only_database(workspace: Path) -> None:
    path = workspace / "vision.db"

    store = DatasetStore.open(path, now=lambda: START)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600
        connection = sqlite3.connect(str(path))
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            connection.close()
    finally:
        store.close()


def test_record_example_then_read_it_back(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        recorded = store.record_example(
            operation_id="op-1",
            slot=3,
            frame_png=b"\x89PNG-fixture",
            frame_captured_at=START - timedelta(seconds=1),
            operation_created_at=START,
        )

        assert recorded is True
        assert store.has_example("op-1")
        assert store.counts_by_slot() == {3: 1}
        assert store.total_examples() == 1
    finally:
        store.close()


def test_recording_the_same_operation_twice_is_a_noop_not_a_duplicate(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        first = store.record_example(
            operation_id="op-1",
            slot=3,
            frame_png=b"first",
            frame_captured_at=START,
            operation_created_at=START,
        )
        second = store.record_example(
            operation_id="op-1",
            slot=5,
            frame_png=b"second",
            frame_captured_at=START,
            operation_created_at=START,
        )

        assert first is True
        assert second is False
        assert store.total_examples() == 1
        assert store.counts_by_slot() == {3: 1}
    finally:
        store.close()


def test_counts_by_slot_across_several_examples(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        for index, slot in enumerate([3, 3, 5, 5, 5, 7]):
            store.record_example(
                operation_id=f"op-{index}",
                slot=slot,
                frame_png=b"x",
                frame_captured_at=START,
                operation_created_at=START,
            )

        assert store.counts_by_slot() == {3: 2, 5: 3, 7: 1}
        assert store.total_examples() == 6
    finally:
        store.close()


def test_a_naive_timestamp_is_refused(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        with pytest.raises(DatasetError, match="timezone-aware"):
            store.record_example(
                operation_id="op-1",
                slot=3,
                frame_png=b"x",
                frame_captured_at=datetime(2026, 8, 12, 12, 0),  # naive
                operation_created_at=START,
            )
    finally:
        store.close()


def test_a_closed_store_refuses_reads_and_writes(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    store.close()

    with pytest.raises(DatasetError, match="closed"):
        store.has_example("op-1")
    with pytest.raises(DatasetError, match="closed"):
        store.record_example(
            operation_id="op-1",
            slot=3,
            frame_png=b"x",
            frame_captured_at=START,
            operation_created_at=START,
        )


def test_an_in_memory_store_works_without_touching_disk() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    try:
        store.record_example(
            operation_id="op-1",
            slot=1,
            frame_png=b"x",
            frame_captured_at=START,
            operation_created_at=START,
        )
        assert store.total_examples() == 1
    finally:
        store.close()


def test_a_newer_unknown_schema_version_is_refused(workspace: Path) -> None:
    path = workspace / "vision.db"
    store = DatasetStore.open(path, now=lambda: START)
    store.close()

    connection = sqlite3.connect(str(path))
    try:
        connection.execute(
            "INSERT INTO schema_migrations (version, name, applied_at, checksum)"
            " VALUES (99, 'from-the-future', ?, 'deadbeef')",
            (START.isoformat(),),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatasetSchemaError, match="does not know"):
        DatasetStore.open(path, now=lambda: START)


def test_a_diverged_migration_checksum_is_refused(workspace: Path) -> None:
    path = workspace / "vision.db"
    store = DatasetStore.open(path, now=lambda: START)
    store.close()

    connection = sqlite3.connect(str(path))
    try:
        connection.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(DatasetSchemaError, match="diverged"):
        DatasetStore.open(path, now=lambda: START)


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


def test_examples_returns_every_recorded_slot_and_frame(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        store.record_example(
            operation_id="op-1",
            slot=3,
            frame_png=b"first",
            frame_captured_at=START,
            operation_created_at=START,
        )
        store.record_example(
            operation_id="op-2",
            slot=5,
            frame_png=b"second",
            frame_captured_at=START,
            operation_created_at=START,
        )

        examples = store.examples()

        assert set(examples) == {
            DatasetExample(slot=3, frame_png=b"first"),
            DatasetExample(slot=5, frame_png=b"second"),
        }
    finally:
        store.close()


def test_examples_is_empty_before_anything_is_recorded(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        assert store.examples() == ()
    finally:
        store.close()


def test_record_candidate_then_read_it_back(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)

        [summary] = store.candidates()
        assert summary.version == version
        assert summary.trained_at == START.isoformat()
        assert summary.included_classes == (3, 5)
        assert summary.excluded_classes == (7,)
        assert summary.accuracy_by_class == {3: 1.0, 5: 0.9}
        assert summary.minimum_examples_per_class == 40
        assert summary.training_example_count == 64
        assert summary.holdout_example_count == 16
        assert store.candidate_model(version) == b"fake-model-bytes"
    finally:
        store.close()


def test_recording_a_second_candidate_never_overwrites_the_first(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        first = store.record_candidate(_candidate(model_blob=b"model-v1"), trained_at=START)
        second = store.record_candidate(
            _candidate(model_blob=b"model-v2"), trained_at=START + timedelta(minutes=5)
        )

        assert second != first
        assert store.candidate_model(first) == b"model-v1"
        assert store.candidate_model(second) == b"model-v2"
        assert [summary.version for summary in store.candidates()] == [first, second]
    finally:
        store.close()


def test_candidate_model_of_an_unknown_version_is_refused(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        with pytest.raises(DatasetError, match="no candidate model"):
            store.candidate_model(999)
    finally:
        store.close()


def test_candidates_is_empty_before_anything_is_trained(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        assert store.candidates() == ()
    finally:
        store.close()


def test_active_version_is_none_before_anything_is_activated(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        assert store.active_version() is None
        assert store.activations() == ()
    finally:
        store.close()


def test_activate_records_an_activation_and_becomes_the_active_version(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)

        activation_id = store.activate(version, activated_at=START + timedelta(minutes=1))

        assert store.active_version() == version
        [activation] = store.activations()
        assert activation.activation_id == activation_id
        assert activation.version == version
        assert activation.activated_at == (START + timedelta(minutes=1)).isoformat()
    finally:
        store.close()


def test_activating_an_unknown_version_is_refused(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        with pytest.raises(DatasetError, match="no candidate model at version 999"):
            store.activate(999, activated_at=START)
        assert store.active_version() is None
    finally:
        store.close()


def test_activating_a_second_version_does_not_edit_the_first_activation(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        first = store.record_candidate(_candidate(model_blob=b"v1"), trained_at=START)
        second = store.record_candidate(_candidate(model_blob=b"v2"), trained_at=START)

        store.activate(first, activated_at=START)
        store.activate(second, activated_at=START + timedelta(minutes=1))

        assert store.active_version() == second
        assert [activation.version for activation in store.activations()] == [first, second]
    finally:
        store.close()


def test_rolling_back_is_just_another_activation_pointing_at_the_earlier_version(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        first = store.record_candidate(_candidate(model_blob=b"v1"), trained_at=START)
        second = store.record_candidate(_candidate(model_blob=b"v2"), trained_at=START)
        store.activate(first, activated_at=START)
        store.activate(second, activated_at=START + timedelta(minutes=1))

        # A "rollback" is not a distinct operation on the store: it is the
        # caller activating whatever version was previously active.
        store.activate(first, activated_at=START + timedelta(minutes=2))

        assert store.active_version() == first
        assert [activation.version for activation in store.activations()] == [
            first,
            second,
            first,
        ]
    finally:
        store.close()


def test_latest_suggestion_is_none_before_anything_is_recorded(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        assert store.latest_suggestion() is None
    finally:
        store.close()


def test_record_suggestion_then_read_it_back(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)

        suggestion_id = store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.87,
            frame_captured_at=START - timedelta(seconds=1),
            suggested_at=START,
        )

        latest = store.latest_suggestion()
        assert latest is not None
        assert latest.suggestion_id == suggestion_id
        assert latest.model_version == version
        assert latest.suggested_slot == 3
        assert latest.confidence == 0.87
        assert latest.primer_present is None
        assert latest.suggested_at == START.isoformat()
    finally:
        store.close()


def test_record_suggestion_stores_a_confidently_clear_primer_reading(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)

        store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.87,
            frame_captured_at=START,
            suggested_at=START,
            primer_present=False,
        )

        latest = store.latest_suggestion()
        assert latest is not None
        assert latest.primer_present is False
    finally:
        store.close()


def test_record_suggestion_stores_a_flagged_primer_reading(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)

        store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.87,
            frame_captured_at=START,
            suggested_at=START,
            primer_present=True,
        )

        latest = store.latest_suggestion()
        assert latest is not None
        assert latest.primer_present is True
    finally:
        store.close()


def test_latest_suggestion_is_the_most_recently_recorded_one(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.5,
            frame_captured_at=START,
            suggested_at=START,
        )
        store.record_suggestion(
            model_version=version,
            suggested_slot=5,
            confidence=0.9,
            frame_captured_at=START + timedelta(seconds=2),
            suggested_at=START + timedelta(seconds=2),
        )

        latest = store.latest_suggestion()
        assert latest is not None
        assert latest.suggested_slot == 5
    finally:
        store.close()


def test_unmatched_suggestion_at_or_before_finds_the_newest_qualifying_one(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        early = store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.5,
            frame_captured_at=START,
            suggested_at=START,
        )
        store.record_suggestion(
            model_version=version,
            suggested_slot=5,
            confidence=0.9,
            frame_captured_at=START + timedelta(minutes=5),
            suggested_at=START + timedelta(minutes=5),
        )

        found = store.unmatched_suggestion_at_or_before(START + timedelta(seconds=1))

        assert found is not None
        assert found.suggestion_id == early
    finally:
        store.close()


def test_unmatched_suggestion_at_or_before_returns_none_without_evidence(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.5,
            frame_captured_at=START + timedelta(minutes=5),
            suggested_at=START + timedelta(minutes=5),
        )

        # The only suggestion recorded is newer than the moment asked about.
        assert store.unmatched_suggestion_at_or_before(START) is None
    finally:
        store.close()


def test_unmatched_suggestion_at_or_before_excludes_already_matched_suggestions(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        suggestion_id = store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.5,
            frame_captured_at=START,
            suggested_at=START,
        )
        store.record_suggestion_outcome(
            suggestion_id=suggestion_id,
            operation_id="op-1",
            actual_slot=3,
            recorded_at=START,
        )

        assert store.unmatched_suggestion_at_or_before(START + timedelta(minutes=1)) is None
    finally:
        store.close()


def test_record_suggestion_outcome_marks_correctness_by_comparing_to_the_suggestion(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        version = store.record_candidate(_candidate(), trained_at=START)
        matching = store.record_suggestion(
            model_version=version,
            suggested_slot=3,
            confidence=0.5,
            frame_captured_at=START,
            suggested_at=START,
        )
        mismatching = store.record_suggestion(
            model_version=version,
            suggested_slot=5,
            confidence=0.5,
            frame_captured_at=START,
            suggested_at=START + timedelta(seconds=1),
        )

        store.record_suggestion_outcome(
            suggestion_id=matching, operation_id="op-1", actual_slot=3, recorded_at=START
        )
        store.record_suggestion_outcome(
            suggestion_id=mismatching, operation_id="op-2", actual_slot=7, recorded_at=START
        )

        accuracy = store.suggestion_accuracy()
        assert accuracy.total == 2
        assert accuracy.correct == 1
        assert accuracy.fraction == pytest.approx(0.5)
    finally:
        store.close()


def test_recording_an_outcome_for_an_unknown_suggestion_is_refused(workspace: Path) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        with pytest.raises(DatasetError, match="no suggestion at id"):
            store.record_suggestion_outcome(
                suggestion_id=999, operation_id="op-1", actual_slot=3, recorded_at=START
            )
    finally:
        store.close()


def test_suggestion_accuracy_fraction_is_none_before_anything_is_matched(
    workspace: Path,
) -> None:
    store = DatasetStore.open(workspace / "vision.db", now=lambda: START)
    try:
        accuracy = store.suggestion_accuracy()

        assert accuracy.total == 0
        assert accuracy.fraction is None
    finally:
        store.close()

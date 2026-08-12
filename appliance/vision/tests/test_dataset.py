from __future__ import annotations

import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cs71vision.dataset import IN_MEMORY, DatasetError, DatasetSchemaError, DatasetStore

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

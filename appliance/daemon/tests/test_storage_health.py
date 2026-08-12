from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cs71d.storage_health import (
    PRODUCTION_BACKUP_MARKER_PATH,
    PRODUCTION_BACKUP_MAX_AGE,
    PRODUCTION_MINIMUM_FREE_BYTES,
    BackupFreshnessMonitor,
    DiskSpaceMonitor,
    DurabilityMonitor,
    DurabilityThreatError,
    production_durability_monitor,
)

START = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def test_disk_space_monitor_passes_when_free_space_is_above_the_floor(tmp_path: Path) -> None:
    free = shutil.disk_usage(tmp_path).free
    monitor = DiskSpaceMonitor(path=tmp_path, minimum_free_bytes=free - 1)

    monitor.check()


def test_disk_space_monitor_refuses_below_the_floor(tmp_path: Path) -> None:
    free = shutil.disk_usage(tmp_path).free
    monitor = DiskSpaceMonitor(path=tmp_path, minimum_free_bytes=free + 1)

    with pytest.raises(DurabilityThreatError, match="below the"):
        monitor.check()


def test_disk_space_monitor_refuses_a_path_it_cannot_stat(tmp_path: Path) -> None:
    monitor = DiskSpaceMonitor(path=tmp_path / "does-not-exist", minimum_free_bytes=0)

    with pytest.raises(DurabilityThreatError, match="cannot read free space"):
        monitor.check()


def _marker(tmp_path: Path, *, ok: bool = True, completed_at: datetime = START) -> Path:
    path = tmp_path / "backup-status.json"
    path.write_text(
        f'{{"ok": {"true" if ok else "false"}, "completed_at": "{completed_at.isoformat()}"}}',
        encoding="utf-8",
    )
    return path


def test_backup_freshness_monitor_passes_for_a_fresh_successful_backup(tmp_path: Path) -> None:
    marker = _marker(tmp_path, completed_at=START)
    monitor = BackupFreshnessMonitor(
        marker_path=marker,
        maximum_age=timedelta(hours=48),
        now=lambda: START + timedelta(hours=1),
    )

    monitor.check()


def test_backup_freshness_monitor_refuses_when_no_backup_has_ever_run(tmp_path: Path) -> None:
    monitor = BackupFreshnessMonitor(
        marker_path=tmp_path / "absent.json",
        maximum_age=timedelta(hours=48),
        now=lambda: START,
    )

    with pytest.raises(DurabilityThreatError, match="no backup has ever completed"):
        monitor.check()


def test_backup_freshness_monitor_refuses_a_failed_backup(tmp_path: Path) -> None:
    marker = _marker(tmp_path, ok=False, completed_at=START)
    monitor = BackupFreshnessMonitor(
        marker_path=marker,
        maximum_age=timedelta(hours=48),
        now=lambda: START,
    )

    with pytest.raises(DurabilityThreatError, match="failed"):
        monitor.check()


def test_backup_freshness_monitor_refuses_a_stale_backup(tmp_path: Path) -> None:
    marker = _marker(tmp_path, completed_at=START)
    monitor = BackupFreshnessMonitor(
        marker_path=marker,
        maximum_age=timedelta(hours=48),
        now=lambda: START + timedelta(hours=49),
    )

    with pytest.raises(DurabilityThreatError, match="older than"):
        monitor.check()


def test_backup_freshness_monitor_refuses_a_malformed_marker(tmp_path: Path) -> None:
    marker = tmp_path / "backup-status.json"
    marker.write_text("not json", encoding="utf-8")
    monitor = BackupFreshnessMonitor(
        marker_path=marker,
        maximum_age=timedelta(hours=48),
        now=lambda: START,
    )

    with pytest.raises(DurabilityThreatError, match="malformed"):
        monitor.check()


def test_backup_freshness_monitor_refuses_a_naive_timestamp(tmp_path: Path) -> None:
    marker = tmp_path / "backup-status.json"
    marker.write_text('{"ok": true, "completed_at": "2026-08-11T12:00:00"}', encoding="utf-8")
    monitor = BackupFreshnessMonitor(
        marker_path=marker,
        maximum_age=timedelta(hours=48),
        now=lambda: START,
    )

    with pytest.raises(DurabilityThreatError, match="naive timestamp"):
        monitor.check()


def test_durability_monitor_raises_on_the_first_failing_check() -> None:
    calls: list[str] = []

    def first() -> None:
        calls.append("first")

    def second() -> None:
        calls.append("second")
        raise DurabilityThreatError("second failed")

    def third() -> None:
        calls.append("third")

    monitor = DurabilityMonitor(checks=(first, second, third))

    with pytest.raises(DurabilityThreatError, match="second failed"):
        monitor.check()
    assert calls == ["first", "second"]


def test_production_durability_monitor_wires_disk_and_backup_checks(tmp_path: Path) -> None:
    database_path = tmp_path / "machine.db"
    monitor = production_durability_monitor(database_path=str(database_path), now=lambda: START)

    assert len(monitor.checks) == 2
    disk_monitor = monitor.checks[0].__self__  # type: ignore[attr-defined]
    backup_monitor = monitor.checks[1].__self__  # type: ignore[attr-defined]
    assert isinstance(disk_monitor, DiskSpaceMonitor)
    assert disk_monitor.path == tmp_path
    assert disk_monitor.minimum_free_bytes == PRODUCTION_MINIMUM_FREE_BYTES
    assert isinstance(backup_monitor, BackupFreshnessMonitor)
    assert backup_monitor.marker_path == Path(PRODUCTION_BACKUP_MARKER_PATH)
    assert backup_monitor.maximum_age == PRODUCTION_BACKUP_MAX_AGE
    assert backup_monitor.now() == START

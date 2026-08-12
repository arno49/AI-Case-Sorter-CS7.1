"""Durability threats beyond a failed journal write: low disk space and a
missing, failed or stale backup.

Both are latched through the same mechanism a journal write failure uses
(``MachineState.record_journal_fault``, via ``OperationDomain``), because
they threaten the same guarantee a journal write does: an operation admitted
now might not be durably recoverable. This module only detects the threat;
it never latches or blocks anything itself.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

#: MVP fixed floor, not operator-configurable: production paths are already
#: fixed constants (see config.py), and a per-install tunable threshold is
#: speculative complexity this appliance does not need yet.
PRODUCTION_MINIMUM_FREE_BYTES = 500 * 1024 * 1024

#: Written by appliance/ops/backup.sh on every attempt, success or failure -
#: never by the daemon, which only ever reads it.
PRODUCTION_BACKUP_MARKER_PATH = "/var/lib/cs71d/backup-status.json"

#: appliance/ops/systemd/cs71-backup.timer runs daily; one missed run of
#: slack before an operator is blocked from new motion over it.
PRODUCTION_BACKUP_MAX_AGE = timedelta(hours=48)


class DurabilityThreatError(RuntimeError):
    """Free disk space or backup freshness has fallen below its floor."""


@dataclass(frozen=True, slots=True)
class DiskSpaceMonitor:
    """Refuse new work once free space on ``path``'s filesystem is critical."""

    path: Path
    minimum_free_bytes: int

    def check(self) -> None:
        try:
            free = shutil.disk_usage(self.path).free
        except OSError as exc:
            raise DurabilityThreatError(f"cannot read free space at {self.path}: {exc}") from exc
        if free < self.minimum_free_bytes:
            raise DurabilityThreatError(
                f"{free} bytes free at {self.path}, below the {self.minimum_free_bytes}-byte floor"
            )


@dataclass(frozen=True, slots=True)
class BackupFreshnessMonitor:
    """Refuse new work once the last successful backup is missing, failed or stale.

    ``marker_path`` is a small JSON document `appliance/ops/backup.sh` writes
    after every attempt: ``{"ok": bool, "completed_at": "<ISO-8601>"}``. This
    monitor only reads it; the backup script is the only writer.
    """

    marker_path: Path
    maximum_age: timedelta
    now: Callable[[], datetime]

    def check(self) -> None:
        try:
            raw = self.marker_path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise DurabilityThreatError(
                f"no backup has ever completed: {self.marker_path} does not exist"
            ) from exc
        except OSError as exc:
            raise DurabilityThreatError(
                f"cannot read backup marker {self.marker_path}: {exc}"
            ) from exc

        try:
            document = json.loads(raw)
            ok = bool(document["ok"])
            completed_at = datetime.fromisoformat(str(document["completed_at"]))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            raise DurabilityThreatError(
                f"backup marker {self.marker_path} is malformed: {exc}"
            ) from exc

        if not ok:
            raise DurabilityThreatError(f"the backup recorded at {completed_at} failed")
        if completed_at.tzinfo is None:
            raise DurabilityThreatError(f"backup marker {self.marker_path} has a naive timestamp")

        age = self.now() - completed_at.astimezone(UTC)
        if age > self.maximum_age:
            raise DurabilityThreatError(
                f"the last successful backup is {age} old, older than the {self.maximum_age} floor"
            )


@dataclass(frozen=True, slots=True)
class DurabilityMonitor:
    """Compose zero or more individual checks; the first threat found wins."""

    checks: tuple[Callable[[], None], ...]

    def check(self) -> None:
        for one in self.checks:
            one()


def production_durability_monitor(
    *,
    database_path: str,
    now: Callable[[], datetime],
) -> DurabilityMonitor:
    """Build the fixed production monitor: disk space beside ``machine.db``,
    plus freshness of the backup marker the installer's timer maintains.
    """
    disk = DiskSpaceMonitor(
        path=Path(database_path).parent,
        minimum_free_bytes=PRODUCTION_MINIMUM_FREE_BYTES,
    )
    backup = BackupFreshnessMonitor(
        marker_path=Path(PRODUCTION_BACKUP_MARKER_PATH),
        maximum_age=PRODUCTION_BACKUP_MAX_AGE,
        now=now,
    )
    return DurabilityMonitor(checks=(disk.check, backup.check))

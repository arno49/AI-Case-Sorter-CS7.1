"""The self-labeled dataset store: `vision.db`.

`cs71-vision` exclusively owns this database, the same separate-ownership
rule `machine.db`/`web.db` already follow
(docs/architecture/data-and-persistence.md): no process reads or writes
another service's database as a shortcut. A row is written only from
confirmed `cs71d` state - a `SUCCEEDED` sort operation's own
`terminal_fields.slot` - never guessed at from a frame alone.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

IN_MEMORY = ":memory:"


class DatasetError(RuntimeError):
    """The dataset store could not complete a required write or read."""


class DatasetSchemaError(DatasetError):
    """The database schema is unknown, newer, or has been altered."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]

    @property
    def checksum(self) -> str:
        return sha256("\n".join(self.statements).encode("utf-8")).hexdigest()


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="examples",
        statements=(
            """
            CREATE TABLE examples (
                operation_id TEXT PRIMARY KEY,
                slot INTEGER NOT NULL,
                frame_png BLOB NOT NULL,
                frame_captured_at TEXT NOT NULL,
                operation_created_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX examples_by_slot ON examples(slot)",
        ),
    ),
    Migration(
        version=2,
        name="models",
        statements=(
            """
            CREATE TABLE models (
                version INTEGER PRIMARY KEY AUTOINCREMENT,
                trained_at TEXT NOT NULL,
                model_blob BLOB NOT NULL,
                included_classes TEXT NOT NULL,
                excluded_classes TEXT NOT NULL,
                accuracy_by_class TEXT NOT NULL,
                minimum_examples_per_class INTEGER NOT NULL,
                training_example_count INTEGER NOT NULL,
                holdout_example_count INTEGER NOT NULL
            )
            """,
        ),
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1].version


@dataclass(frozen=True, slots=True)
class DatasetExample:
    """One labeled example, reduced to what the training pipeline needs."""

    slot: int
    frame_png: bytes


@dataclass(frozen=True, slots=True)
class TrainedCandidate:
    """A freshly trained model, ready to be recorded as a new version.

    Produced by `cs71vision.training.train_candidate`; this module only
    knows its shape, not how it was produced, the same separation
    `Correlator`/`DatasetStore` already keep.
    """

    model_blob: bytes
    included_classes: tuple[int, ...]
    excluded_classes: tuple[int, ...]
    #: Per included class that had at least one held-out example. A class
    #: with too few examples to split (see `train_candidate`) has no entry
    #: here rather than a fabricated value.
    accuracy_by_class: Mapping[int, float]
    minimum_examples_per_class: int
    training_example_count: int
    holdout_example_count: int


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    """A recorded candidate's metadata, without its (potentially large) model blob."""

    version: int
    trained_at: str
    included_classes: tuple[int, ...]
    excluded_classes: tuple[int, ...]
    accuracy_by_class: Mapping[int, float]
    minimum_examples_per_class: int
    training_example_count: int
    holdout_example_count: int


_CREATE_MIGRATION_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL
)
"""


class DatasetStore:
    """Own one `vision.db` connection and record labeled examples.

    Shared across threads under a lock, the same way `cs71d.journal.Journal`
    is: the capture loop and the correlation loop are different threads, and
    SQLite must not see concurrent use of one connection.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        path: str,
        now: Callable[[], datetime],
    ) -> None:
        self._connection = connection
        self._path = path
        self._now = now
        self._lock = threading.Lock()
        self._closed = False

    @classmethod
    def open(cls, path: str | Path, *, now: Callable[[], datetime] | None = None) -> DatasetStore:
        """Open or create the dataset store at `path` and apply migrations."""
        location = IN_MEMORY if str(path) == IN_MEMORY else str(Path(path))
        try:
            connection = sqlite3.connect(location, check_same_thread=False, isolation_level=None)
        except sqlite3.Error as exc:
            raise DatasetError(f"cannot open dataset store {location}: {exc}") from exc

        store = cls(connection, path=location, now=now or _utcnow)
        try:
            store._configure()
            store._migrate()
        except Exception:
            connection.close()
            raise
        return store

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()

    def __enter__(self) -> DatasetStore:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def record_example(
        self,
        *,
        operation_id: str,
        slot: int,
        frame_png: bytes,
        frame_captured_at: datetime,
        operation_created_at: datetime,
    ) -> bool:
        """Insert one labeled example. Returns False if already recorded.

        Idempotent by design: `operation_id` is the primary key, so polling
        the same `cs71d` operation more than once (the correlator's own
        normal behavior) never creates a duplicate example.
        """
        with self._transaction() as cursor:
            try:
                cursor.execute(
                    "INSERT INTO examples"
                    " (operation_id, slot, frame_png, frame_captured_at,"
                    " operation_created_at, recorded_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        operation_id,
                        slot,
                        frame_png,
                        _encode_time(frame_captured_at),
                        _encode_time(operation_created_at),
                        _encode_time(self._now()),
                    ),
                )
            except sqlite3.IntegrityError:
                return False
        return True

    def has_example(self, operation_id: str) -> bool:
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT 1 FROM examples WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        return row is not None

    def counts_by_slot(self) -> Mapping[int, int]:
        """Per-class example counts, without ever reading a frame column."""
        with self._guard() as cursor:
            rows = cursor.execute(
                "SELECT slot, COUNT(*) AS n FROM examples GROUP BY slot ORDER BY slot"
            ).fetchall()
        return {int(row["slot"]): int(row["n"]) for row in rows}

    def total_examples(self) -> int:
        with self._guard() as cursor:
            row = cursor.execute("SELECT COUNT(*) AS n FROM examples").fetchone()
        return int(row["n"])

    def examples(self) -> tuple[DatasetExample, ...]:
        """Every labeled example, slot and frame only - what training needs.

        Reads everything into memory in one guarded pass rather than holding
        a cursor open across the caller's own (likely long) training run:
        the lock this takes is brief, the computation the caller does with
        the result is not, and those must not be conflated.
        """
        with self._guard() as cursor:
            rows = cursor.execute(
                "SELECT slot, frame_png FROM examples ORDER BY recorded_at"
            ).fetchall()
        return tuple(
            DatasetExample(slot=int(row["slot"]), frame_png=bytes(row["frame_png"])) for row in rows
        )

    def record_candidate(self, candidate: TrainedCandidate, *, trained_at: datetime) -> int:
        """Store a newly trained candidate as a new version.

        Never overwrites a prior version (`AUTOINCREMENT` primary key, plain
        `INSERT`): PI-VISION-004's own requirement is that the previously
        active version is retained, not replaced in place.
        """
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO models"
                " (trained_at, model_blob, included_classes, excluded_classes, accuracy_by_class,"
                " minimum_examples_per_class, training_example_count, holdout_example_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _encode_time(trained_at),
                    candidate.model_blob,
                    json.dumps(list(candidate.included_classes)),
                    json.dumps(list(candidate.excluded_classes)),
                    json.dumps(
                        {
                            str(slot): accuracy
                            for slot, accuracy in candidate.accuracy_by_class.items()
                        }
                    ),
                    candidate.minimum_examples_per_class,
                    candidate.training_example_count,
                    candidate.holdout_example_count,
                ),
            )
            version = cursor.lastrowid
        if version is None:
            raise DatasetError("recording a candidate did not yield a version")
        return version

    def candidates(self) -> tuple[CandidateSummary, ...]:
        """Every recorded candidate's metadata, newest last, no model blobs."""
        with self._guard() as cursor:
            rows = cursor.execute(
                "SELECT version, trained_at, included_classes, excluded_classes, accuracy_by_class,"
                " minimum_examples_per_class, training_example_count, holdout_example_count"
                " FROM models ORDER BY version"
            ).fetchall()
        return tuple(_candidate_summary_from(row) for row in rows)

    def candidate_model(self, version: int) -> bytes:
        """One recorded candidate's own model blob, by version."""
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT model_blob FROM models WHERE version = ?", (version,)
            ).fetchone()
        if row is None:
            raise DatasetError(f"no candidate model at version {version}")
        return bytes(row["model_blob"])

    def _configure(self) -> None:
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
            if self._path != IN_MEMORY:
                mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise DatasetError(f"dataset store {self._path} refused WAL mode (got {mode})")
                os.chmod(self._path, 0o600)
        except sqlite3.Error as exc:
            raise DatasetError(f"cannot configure dataset store {self._path}: {exc}") from exc

    def _migrate(self) -> None:
        with self._transaction() as cursor:
            cursor.execute(_CREATE_MIGRATION_LEDGER)
            applied = {
                int(row["version"]): (str(row["name"]), str(row["checksum"]))
                for row in cursor.execute(
                    "SELECT version, name, checksum FROM schema_migrations"
                ).fetchall()
            }

        known = {migration.version for migration in MIGRATIONS}
        unknown = sorted(set(applied) - known)
        if unknown:
            raise DatasetSchemaError(
                f"dataset store {self._path} has schema versions this build does not know:"
                f" {unknown}; forward-only migration cannot downgrade in place"
            )

        for migration in MIGRATIONS:
            recorded = applied.get(migration.version)
            if recorded is not None:
                if recorded[1] != migration.checksum:
                    raise DatasetSchemaError(
                        f"migration {migration.version} ({recorded[0]}) was applied with a"
                        " different definition; the schema has diverged"
                    )
                continue
            with self._transaction() as cursor:
                for statement in migration.statements:
                    cursor.execute(statement)
                cursor.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at, checksum)"
                    " VALUES (?, ?, ?, ?)",
                    (
                        migration.version,
                        migration.name,
                        _encode_time(self._now()),
                        migration.checksum,
                    ),
                )

    @contextmanager
    def _guard(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            self._require_open()
            try:
                yield self._connection.cursor()
            except sqlite3.Error as exc:
                raise DatasetError(f"dataset read failed: {exc}") from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        with self._lock:
            self._require_open()
            cursor = self._connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
            except BaseException as exc:
                cursor.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    raise DatasetError(f"dataset write failed: {exc}") from exc
                raise
            else:
                try:
                    cursor.execute("COMMIT")
                except sqlite3.Error as exc:
                    raise DatasetError(f"dataset commit failed: {exc}") from exc

    def _require_open(self) -> None:
        if self._closed:
            raise DatasetError("dataset store is closed")


def _encode_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise DatasetError("dataset timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _candidate_summary_from(row: sqlite3.Row) -> CandidateSummary:
    return CandidateSummary(
        version=int(row["version"]),
        trained_at=str(row["trained_at"]),
        included_classes=tuple(json.loads(row["included_classes"])),
        excluded_classes=tuple(json.loads(row["excluded_classes"])),
        accuracy_by_class={
            int(slot): float(accuracy)
            for slot, accuracy in json.loads(row["accuracy_by_class"]).items()
        },
        minimum_examples_per_class=int(row["minimum_examples_per_class"]),
        training_example_count=int(row["training_example_count"]),
        holdout_example_count=int(row["holdout_example_count"]),
    )

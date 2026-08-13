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
    Migration(
        version=3,
        name="activations",
        statements=(
            """
            CREATE TABLE activations (
                activation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                version INTEGER NOT NULL REFERENCES models(version),
                activated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        version=4,
        name="suggestions",
        statements=(
            """
            CREATE TABLE suggestions (
                suggestion_id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_version INTEGER NOT NULL REFERENCES models(version),
                suggested_slot INTEGER NOT NULL,
                confidence REAL NOT NULL,
                frame_captured_at TEXT NOT NULL,
                suggested_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE suggestion_outcomes (
                outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL UNIQUE REFERENCES suggestions(suggestion_id),
                operation_id TEXT NOT NULL UNIQUE,
                actual_slot INTEGER NOT NULL,
                correct INTEGER NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        version=5,
        name="suggestion_primer_axis",
        statements=(
            # Nullable: NULL means unknown/ambiguous (the only value this
            # codebase ever writes today, PI-VISION-010), 0/1 mean a
            # confidently clear/flagged reading from a future trained
            # detector this task does not build.
            "ALTER TABLE suggestions ADD COLUMN primer_present INTEGER",
        ),
    ),
    Migration(
        version=6,
        name="autonomous_sort",
        statements=(
            # One row per suggestion actually submitted as an autonomous
            # sort (PI-VISION-008) - `suggestion_id UNIQUE` makes a second
            # attempt against the same suggestion impossible to record, the
            # same discipline `suggestion_outcomes` already uses.
            """
            CREATE TABLE autonomous_attempts (
                attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
                suggestion_id INTEGER NOT NULL UNIQUE REFERENCES suggestions(suggestion_id),
                operation_id TEXT NOT NULL UNIQUE,
                slot INTEGER NOT NULL,
                attempted_at TEXT NOT NULL
            )
            """,
            # A human-recorded verdict, never inferred: an autonomous sort
            # has no operator confirmation to compare against, so the only
            # honest source of "was this actually right" is a person who
            # later checked (ADR-0013: "recorded... not a general 'it seems
            # to work'"). `attempt_id UNIQUE` means one verdict per attempt.
            """
            CREATE TABLE autonomous_reviews (
                review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempt_id INTEGER NOT NULL UNIQUE REFERENCES autonomous_attempts(attempt_id),
                correct INTEGER NOT NULL,
                reviewed_at TEXT NOT NULL
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


@dataclass(frozen=True, slots=True)
class ActivationSummary:
    """One activation event: which version became active, and when.

    Append-only, the same discipline `web_audit` uses: "roll back" is never
    an edit to a prior row, it is a new row pointing at an earlier version.
    """

    activation_id: int
    version: int
    activated_at: str


@dataclass(frozen=True, slots=True)
class SuggestionRecord:
    """One read-only classification of the latest captured frame (PI-VISION-006)."""

    suggestion_id: int
    model_version: int
    suggested_slot: int
    confidence: float
    #: The primer-presence axis (PI-VISION-010). `None` is unknown/ambiguous,
    #: not "no primer" - see `cs71vision.primer.requires_operator_confirmation`.
    primer_present: bool | None
    frame_captured_at: str
    suggested_at: str


@dataclass(frozen=True, slots=True)
class SuggestionAccuracy:
    """Live suggestion accuracy: suggestion versus the operator's actual choice.

    Deliberately separate evidence from a candidate's training-time held-out
    accuracy (`CandidateSummary.accuracy_by_class`) - this is measured
    against real operator decisions after activation, not a held-out split
    of the training data.
    """

    total: int
    correct: int

    @property
    def fraction(self) -> float | None:
        return None if self.total == 0 else self.correct / self.total


@dataclass(frozen=True, slots=True)
class AutonomousAttempt:
    """One suggestion that crossed its class's threshold and was submitted
    to `cs71d` as an autonomous sort (PI-VISION-008), before any human has
    said whether it was actually right.
    """

    attempt_id: int
    suggestion_id: int
    operation_id: str
    slot: int
    attempted_at: str


@dataclass(frozen=True, slots=True)
class AutonomousAccuracy:
    """One class's reviewed autonomous-sort record: the false-autonomous-sort
    rate ADR-0013 requires be measured and recorded before that class's
    threshold may be lowered - never a general "it seems to work."

    `total`/`correct` count only *reviewed* attempts: an attempt nobody has
    checked yet contributes to neither, the same "no evidence, not a guess"
    posture `SuggestionAccuracy.fraction`/`Correlator` already keep.
    """

    slot: int
    total: int
    correct: int

    @property
    def false_rate(self) -> float | None:
        return None if self.total == 0 else (self.total - self.correct) / self.total


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

    def activate(self, version: int, *, activated_at: datetime) -> int:
        """Make `version` the active model by recording a new activation event.

        Never edits a prior activation row - "roll back" is a new activation
        pointing at an earlier version, the same append-only discipline
        `web_audit` uses. Refuses a version this store never recorded a
        candidate for; the `REFERENCES models(version)` foreign key backs
        this with a second, structural guarantee, but this check exists for
        the clearer message.
        """
        with self._transaction() as cursor:
            exists = cursor.execute("SELECT 1 FROM models WHERE version = ?", (version,)).fetchone()
            if exists is None:
                raise DatasetError(f"no candidate model at version {version}")
            cursor.execute(
                "INSERT INTO activations (version, activated_at) VALUES (?, ?)",
                (version, _encode_time(activated_at)),
            )
            activation_id = cursor.lastrowid
        if activation_id is None:
            raise DatasetError("activating a candidate did not yield an activation id")
        return activation_id

    def activations(self) -> tuple[ActivationSummary, ...]:
        """Every activation event, oldest first - the full activate/rollback history."""
        with self._guard() as cursor:
            rows = cursor.execute(
                "SELECT activation_id, version, activated_at"
                " FROM activations ORDER BY activation_id"
            ).fetchall()
        return tuple(
            ActivationSummary(
                activation_id=int(row["activation_id"]),
                version=int(row["version"]),
                activated_at=str(row["activated_at"]),
            )
            for row in rows
        )

    def active_version(self) -> int | None:
        """The version of the most recent activation, or None if never activated."""
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT version FROM activations ORDER BY activation_id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else int(row["version"])

    def record_suggestion(
        self,
        *,
        model_version: int,
        suggested_slot: int,
        confidence: float,
        frame_captured_at: datetime,
        suggested_at: datetime,
        primer_present: bool | None = None,
    ) -> int:
        """Record one read-only classification. Always a new row, never an edit.

        `primer_present` defaults to `None` (unknown/ambiguous) rather than
        being required - the safe reading, not a convenience one (PI-VISION-010):
        a caller that forgets to pass it gets the conservative value, never
        an unsafe one.
        """
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO suggestions"
                " (model_version, suggested_slot, confidence, primer_present,"
                " frame_captured_at, suggested_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    model_version,
                    suggested_slot,
                    confidence,
                    None if primer_present is None else int(primer_present),
                    _encode_time(frame_captured_at),
                    _encode_time(suggested_at),
                ),
            )
            suggestion_id = cursor.lastrowid
        if suggestion_id is None:
            raise DatasetError("recording a suggestion did not yield a suggestion id")
        return suggestion_id

    def latest_suggestion(self) -> SuggestionRecord | None:
        """The most recently recorded suggestion, or None if none exists yet."""
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT suggestion_id, model_version, suggested_slot, confidence, primer_present,"
                " frame_captured_at, suggested_at"
                " FROM suggestions ORDER BY suggestion_id DESC LIMIT 1"
            ).fetchone()
        return None if row is None else _suggestion_record_from(row)

    def unmatched_suggestion_at_or_before(self, moment: datetime) -> SuggestionRecord | None:
        """The newest suggestion at or before `moment` with no recorded outcome yet.

        Mirrors `FrameBuffer.closest_at_or_before`'s own contract: `None`
        means no evidence, never a guess.
        """
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT s.suggestion_id, s.model_version, s.suggested_slot, s.confidence,"
                " s.primer_present, s.frame_captured_at, s.suggested_at"
                " FROM suggestions s"
                " LEFT JOIN suggestion_outcomes o ON o.suggestion_id = s.suggestion_id"
                " WHERE o.suggestion_id IS NULL AND s.suggested_at <= ?"
                " ORDER BY s.suggested_at DESC LIMIT 1",
                (_encode_time(moment),),
            ).fetchone()
        return None if row is None else _suggestion_record_from(row)

    def record_suggestion_outcome(
        self, *, suggestion_id: int, operation_id: str, actual_slot: int, recorded_at: datetime
    ) -> int:
        """Record what actually happened for one suggestion. Never edited afterward."""
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT suggested_slot FROM suggestions WHERE suggestion_id = ?", (suggestion_id,)
            ).fetchone()
            if row is None:
                raise DatasetError(f"no suggestion at id {suggestion_id}")
            correct = int(row["suggested_slot"]) == actual_slot
            cursor.execute(
                "INSERT INTO suggestion_outcomes"
                " (suggestion_id, operation_id, actual_slot, correct, recorded_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (suggestion_id, operation_id, actual_slot, int(correct), _encode_time(recorded_at)),
            )
            outcome_id = cursor.lastrowid
        if outcome_id is None:
            raise DatasetError("recording a suggestion outcome did not yield an outcome id")
        return outcome_id

    def suggestion_accuracy(self) -> SuggestionAccuracy:
        """Live suggestion accuracy across every matched outcome so far."""
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(correct), 0) AS correct"
                " FROM suggestion_outcomes"
            ).fetchone()
        return SuggestionAccuracy(total=int(row["total"]), correct=int(row["correct"]))

    def autonomous_attempt_exists(self, suggestion_id: int) -> bool:
        """Whether this suggestion has already been submitted autonomously.

        `Autonomist.attempt_once` checks this before ever calling `cs71d`,
        so a suggestion still latest on a later tick is never submitted
        twice (PI-VISION-008).
        """
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT 1 FROM autonomous_attempts WHERE suggestion_id = ?", (suggestion_id,)
            ).fetchone()
        return row is not None

    def record_autonomous_attempt(
        self, *, suggestion_id: int, operation_id: str, slot: int, attempted_at: datetime
    ) -> int:
        """Record one autonomous sort submission. Always a new row, never an edit."""
        with self._transaction() as cursor:
            cursor.execute(
                "INSERT INTO autonomous_attempts"
                " (suggestion_id, operation_id, slot, attempted_at) VALUES (?, ?, ?, ?)",
                (suggestion_id, operation_id, slot, _encode_time(attempted_at)),
            )
            attempt_id = cursor.lastrowid
        if attempt_id is None:
            raise DatasetError("recording an autonomous attempt did not yield an attempt id")
        return attempt_id

    def pending_autonomous_reviews(self) -> tuple[AutonomousAttempt, ...]:
        """Every autonomous attempt no human has recorded a verdict for yet."""
        with self._guard() as cursor:
            rows = cursor.execute(
                "SELECT a.attempt_id, a.suggestion_id, a.operation_id, a.slot, a.attempted_at"
                " FROM autonomous_attempts a"
                " LEFT JOIN autonomous_reviews r ON r.attempt_id = a.attempt_id"
                " WHERE r.attempt_id IS NULL"
                " ORDER BY a.attempt_id ASC"
            ).fetchall()
        return tuple(
            AutonomousAttempt(
                attempt_id=int(row["attempt_id"]),
                suggestion_id=int(row["suggestion_id"]),
                operation_id=str(row["operation_id"]),
                slot=int(row["slot"]),
                attempted_at=str(row["attempted_at"]),
            )
            for row in rows
        )

    def record_autonomous_review(
        self, *, attempt_id: int, correct: bool, reviewed_at: datetime
    ) -> int:
        """Record a human's verdict on one autonomous attempt. Never edited afterward."""
        with self._transaction() as cursor:
            row = cursor.execute(
                "SELECT 1 FROM autonomous_attempts WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise DatasetError(f"no autonomous attempt at id {attempt_id}")
            cursor.execute(
                "INSERT INTO autonomous_reviews (attempt_id, correct, reviewed_at)"
                " VALUES (?, ?, ?)",
                (attempt_id, int(correct), _encode_time(reviewed_at)),
            )
            review_id = cursor.lastrowid
        if review_id is None:
            raise DatasetError("recording an autonomous review did not yield a review id")
        return review_id

    def autonomous_accuracy_by_class(self) -> tuple[AutonomousAccuracy, ...]:
        """The reviewed false-autonomous-sort record for every class with a review.

        A class with autonomous attempts but zero reviews yet is absent
        here, not reported as 0% false: "not yet reviewed" and "reviewed
        clean" must never look the same (ADR-0013's own "not a general 'it
        seems to work'").
        """
        with self._guard() as cursor:
            rows = cursor.execute(
                "SELECT a.slot AS slot, COUNT(*) AS total, COALESCE(SUM(r.correct), 0) AS correct"
                " FROM autonomous_reviews r"
                " JOIN autonomous_attempts a ON a.attempt_id = r.attempt_id"
                " GROUP BY a.slot"
                " ORDER BY a.slot ASC"
            ).fetchall()
        return tuple(
            AutonomousAccuracy(
                slot=int(row["slot"]), total=int(row["total"]), correct=int(row["correct"])
            )
            for row in rows
        )

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


def _suggestion_record_from(row: sqlite3.Row) -> SuggestionRecord:
    primer_present = row["primer_present"]
    return SuggestionRecord(
        suggestion_id=int(row["suggestion_id"]),
        model_version=int(row["model_version"]),
        suggested_slot=int(row["suggested_slot"]),
        confidence=float(row["confidence"]),
        primer_present=None if primer_present is None else bool(primer_present),
        frame_captured_at=str(row["frame_captured_at"]),
        suggested_at=str(row["suggested_at"]),
    )

"""Durable SQLite journal for daemon operations.

`cs71d` exclusively owns ``machine.db``. A journal write required for operation
admission, a lifecycle transition or a terminal outcome must succeed before
that transition may be reported as durable, so every method here either commits
or raises; nothing is reported optimistically.

The schema is forward-only and versioned. Two invariants are enforced by the
storage engine itself rather than by convention:

* ``operation_transitions`` is append-only -- update and delete abort.
* an operation row may not enter ``succeeded`` without a trusted firmware
  terminal, mirroring SAF-02 at the durability layer.
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

from .operations import (
    Actor,
    IdempotencyRecord,
    InvalidTransitionError,
    OperationAction,
    OperationRecord,
    OperationState,
    TransitionRecord,
    ValidationError,
    can_transition,
    is_terminal,
    require_reason,
    require_terminal_fields,
)

IN_MEMORY = ":memory:"


class JournalError(RuntimeError):
    """The durable journal could not complete a required write or read."""


class JournalSchemaError(JournalError):
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
        name="operations_and_idempotency",
        statements=(
            """
            CREATE TABLE operations (
                operation_id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                state TEXT NOT NULL,
                generation INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                deadline_at TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                trusted_terminal INTEGER NOT NULL DEFAULT 0,
                outcome TEXT,
                terminal_at TEXT,
                protocol_request_id INTEGER
            )
            """,
            """
            CREATE TABLE operation_transitions (
                transition_id INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                from_state TEXT,
                to_state TEXT NOT NULL,
                generation INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                reason TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE idempotency_records (
                key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX operation_transitions_by_operation"
            " ON operation_transitions(operation_id, transition_id)",
            "CREATE INDEX idempotency_records_by_expiry ON idempotency_records(expires_at)",
            """
            CREATE TRIGGER operation_transitions_no_update
            BEFORE UPDATE ON operation_transitions
            BEGIN
                SELECT RAISE(ABORT, 'operation_transitions is append-only');
            END
            """,
            """
            CREATE TRIGGER operation_transitions_no_delete
            BEFORE DELETE ON operation_transitions
            BEGIN
                SELECT RAISE(ABORT, 'operation_transitions is append-only');
            END
            """,
            """
            CREATE TRIGGER operations_insert_requires_trusted_terminal
            BEFORE INSERT ON operations
            WHEN NEW.state = 'succeeded' AND NEW.trusted_terminal <> 1
            BEGIN
                SELECT RAISE(ABORT, 'succeeded requires a trusted firmware terminal');
            END
            """,
            """
            CREATE TRIGGER operations_update_requires_trusted_terminal
            BEFORE UPDATE ON operations
            WHEN NEW.state = 'succeeded' AND NEW.trusted_terminal <> 1
            BEGIN
                SELECT RAISE(ABORT, 'succeeded requires a trusted firmware terminal');
            END
            """,
        ),
    ),
    Migration(
        version=2,
        name="operation_terminal_fields",
        statements=("ALTER TABLE operations ADD COLUMN terminal_fields TEXT",),
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1].version

_CREATE_MIGRATION_LEDGER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    checksum TEXT NOT NULL
)
"""

_OPERATION_COLUMNS = (
    "operation_id, action, fingerprint, state, generation, created_at, deadline_at,"
    " actor_user_id, actor_role, trusted_terminal, outcome, terminal_at, protocol_request_id,"
    " terminal_fields"
)


class Journal:
    """Own one ``machine.db`` connection and record durable operation state.

    The connection is shared across threads under a lock: admission runs on a
    caller thread while lifecycle transitions are recorded by the serial worker
    thread, and SQLite must not see concurrent use of one connection.
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
    def open(
        cls,
        path: str | Path,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> Journal:
        """Open or create the journal at ``path`` and apply pending migrations."""
        location = IN_MEMORY if str(path) == IN_MEMORY else str(Path(path))
        try:
            connection = sqlite3.connect(location, check_same_thread=False, isolation_level=None)
        except sqlite3.Error as exc:
            raise JournalError(f"cannot open journal {location}: {exc}") from exc

        journal = cls(connection, path=location, now=now or _utcnow)
        try:
            journal._configure()
            journal._migrate()
        except Exception:
            connection.close()
            raise
        return journal

    @property
    def path(self) -> str:
        return self._path

    @property
    def schema_version(self) -> int:
        with self._guard() as cursor:
            row = cursor.execute("SELECT max(version) AS version FROM schema_migrations").fetchone()
        version = row["version"]
        return 0 if version is None else int(version)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._connection.close()

    def __enter__(self) -> Journal:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def record_admission(
        self,
        operation: OperationRecord,
        *,
        reason: str,
        idempotency: IdempotencyRecord | None = None,
    ) -> None:
        """Durably admit a new operation, its first transition and its key.

        The three rows land in one transaction, so a key can never outlive the
        operation it deduplicates to, and an admitted operation always has an
        audit entry explaining why it exists.
        """
        if operation.state is not OperationState.QUEUED:
            raise InvalidTransitionError("an admitted operation must start queued")
        require_reason(reason)
        if idempotency is not None:
            if idempotency.operation_id != operation.operation_id:
                raise ValidationError("idempotency record must reference its own operation")
            if idempotency.fingerprint != operation.fingerprint:
                raise ValidationError("idempotency record must carry the request fingerprint")

        with self._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO operations ({_OPERATION_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    operation.operation_id,
                    operation.action.value,
                    operation.fingerprint,
                    operation.state.value,
                    operation.generation,
                    _encode_time(operation.created_at),
                    _encode_time(operation.deadline_at),
                    operation.actor.user_id,
                    operation.actor.role,
                    int(operation.trusted_terminal),
                    operation.outcome,
                    _encode_optional_time(operation.terminal_at),
                    operation.protocol_request_id,
                    _encode_fields(operation.terminal_fields),
                ),
            )
            self._append_transition(
                cursor,
                operation_id=operation.operation_id,
                from_state=None,
                to_state=operation.state,
                generation=operation.generation,
                occurred_at=operation.created_at,
                reason=reason,
            )
            if idempotency is not None:
                cursor.execute(
                    "INSERT INTO idempotency_records"
                    " (key, fingerprint, operation_id, created_at, expires_at)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        idempotency.key,
                        idempotency.fingerprint,
                        idempotency.operation_id,
                        _encode_time(idempotency.created_at),
                        _encode_time(idempotency.expires_at),
                    ),
                )

    def record_transition(
        self,
        operation_id: str,
        *,
        to_state: OperationState,
        generation: int,
        occurred_at: datetime,
        reason: str,
        trusted_terminal: bool = False,
        outcome: str | None = None,
        protocol_request_id: int | None = None,
        terminal_fields: Mapping[str, str] | None = None,
    ) -> OperationRecord:
        """Record one lifecycle transition and return the updated operation.

        ``protocol_request_id`` is diagnostic session-scoped metadata. It is
        stored for correlation with a serial transcript and is never used to
        find, match or deduplicate an operation.
        """
        require_reason(reason)
        if to_state is OperationState.SUCCEEDED and not trusted_terminal:
            raise InvalidTransitionError(
                "succeeded requires a trusted correlated firmware terminal"
            )
        if trusted_terminal and to_state is not OperationState.SUCCEEDED:
            raise InvalidTransitionError("only a succeeded operation records a trusted terminal")

        with self._transaction() as cursor:
            current = self._read_operation(cursor, operation_id)
            if current is None:
                raise JournalError(f"unknown operation {operation_id}")
            if not can_transition(current.state, to_state):
                raise InvalidTransitionError(
                    f"operation {operation_id} cannot move from {current.state} to {to_state}"
                )
            if generation < current.generation:
                raise InvalidTransitionError("snapshot generation must not move backwards")

            moment = _require_moment(occurred_at)
            terminal_at = moment if is_terminal(to_state) else None
            cursor.execute(
                "UPDATE operations SET state = ?, generation = ?, trusted_terminal = ?,"
                " outcome = ?, terminal_at = ?,"
                " protocol_request_id = coalesce(?, protocol_request_id),"
                " terminal_fields = coalesce(?, terminal_fields)"
                " WHERE operation_id = ?",
                (
                    to_state.value,
                    generation,
                    int(trusted_terminal),
                    outcome,
                    _encode_optional_time(terminal_at),
                    protocol_request_id,
                    _encode_fields(
                        None
                        if terminal_fields is None
                        else require_terminal_fields(terminal_fields)
                    ),
                    operation_id,
                ),
            )
            self._append_transition(
                cursor,
                operation_id=operation_id,
                from_state=current.state,
                to_state=to_state,
                generation=generation,
                occurred_at=moment,
                reason=reason,
            )
            updated = self._read_operation(cursor, operation_id)
        if updated is None:  # pragma: no cover - the row was just written
            raise JournalError(f"operation {operation_id} disappeared during transition")
        return updated

    def operation(self, operation_id: str) -> OperationRecord | None:
        with self._guard() as cursor:
            return self._read_operation(cursor, operation_id)

    def recent_operations(
        self,
        *,
        limit: int,
        before: int | None = None,
        state: OperationState | None = None,
        action: OperationAction | None = None,
    ) -> tuple[tuple[int, OperationRecord], ...]:
        """Return a bounded newest-first page of operations with their cursors.

        The cursor is the storage row identity, which is stable and strictly
        increasing with admission order. It is opaque to callers and is not an
        operation identity.
        """
        if limit < 1:
            raise ValidationError("limit must be positive")
        clauses = ["1 = 1"]
        parameters: list[object] = []
        if before is not None:
            clauses.append("rowid < ?")
            parameters.append(before)
        if state is not None:
            clauses.append("state = ?")
            parameters.append(state.value)
        if action is not None:
            clauses.append("action = ?")
            parameters.append(action.value)
        parameters.append(limit)
        with self._guard() as cursor:
            rows = cursor.execute(
                f"SELECT rowid AS cursor, {_OPERATION_COLUMNS} FROM operations"
                f" WHERE {' AND '.join(clauses)} ORDER BY rowid DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return tuple((int(row["cursor"]), _decode_operation(row)) for row in rows)

    def transitions(self, operation_id: str) -> tuple[TransitionRecord, ...]:
        with self._guard() as cursor:
            rows = cursor.execute(
                "SELECT transition_id, operation_id, from_state, to_state, generation,"
                " occurred_at, reason FROM operation_transitions"
                " WHERE operation_id = ? ORDER BY transition_id",
                (operation_id,),
            ).fetchall()
        return tuple(_decode_transition(row) for row in rows)

    def idempotency_record(self, key: str, *, at: datetime) -> IdempotencyRecord | None:
        """Return the live record for ``key``, or ``None`` when absent or expired.

        An expired record is not returned and not deleted here: pruning is a
        separate, observable transaction rather than a side effect of a read.
        """
        with self._guard() as cursor:
            row = cursor.execute(
                "SELECT key, fingerprint, operation_id, created_at, expires_at"
                " FROM idempotency_records WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        record = _decode_idempotency(row)
        return record if record.live_at(_require_moment(at)) else None

    def prune_idempotency(self, *, at: datetime) -> int:
        """Drop expired deduplication entries and return how many were removed.

        Every stored timestamp is normalized to a UTC ISO-8601 string, so text
        comparison and chronological comparison agree.
        """
        moment = _encode_time(_require_moment(at))
        with self._transaction() as cursor:
            cursor.execute("DELETE FROM idempotency_records WHERE expires_at <= ?", (moment,))
            return cursor.rowcount

    def _configure(self) -> None:
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA synchronous = FULL")
            if self._path != IN_MEMORY:
                mode = self._connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if str(mode).lower() != "wal":
                    raise JournalError(f"journal {self._path} refused WAL mode (got {mode})")
                os.chmod(self._path, 0o600)
        except sqlite3.Error as exc:
            raise JournalError(f"cannot configure journal {self._path}: {exc}") from exc

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
            raise JournalSchemaError(
                f"journal {self._path} has schema versions this daemon does not know: {unknown};"
                " forward-only migration cannot downgrade in place"
            )

        for migration in MIGRATIONS:
            recorded = applied.get(migration.version)
            if recorded is not None:
                if recorded[1] != migration.checksum:
                    raise JournalSchemaError(
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

    def _append_transition(
        self,
        cursor: sqlite3.Cursor,
        *,
        operation_id: str,
        from_state: OperationState | None,
        to_state: OperationState,
        generation: int,
        occurred_at: datetime,
        reason: str,
    ) -> None:
        cursor.execute(
            "INSERT INTO operation_transitions"
            " (operation_id, from_state, to_state, generation, occurred_at, reason)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                operation_id,
                None if from_state is None else from_state.value,
                to_state.value,
                generation,
                _encode_time(occurred_at),
                reason,
            ),
        )

    def _read_operation(self, cursor: sqlite3.Cursor, operation_id: str) -> OperationRecord | None:
        row = cursor.execute(
            f"SELECT {_OPERATION_COLUMNS} FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        return None if row is None else _decode_operation(row)

    @contextmanager
    def _guard(self) -> Iterator[sqlite3.Cursor]:
        """Run one read under the connection lock."""
        with self._lock:
            self._require_open()
            try:
                yield self._connection.cursor()
            except sqlite3.Error as exc:
                raise JournalError(f"journal read failed: {exc}") from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        """Run one bounded write transaction under the connection lock."""
        with self._lock:
            self._require_open()
            cursor = self._connection.cursor()
            try:
                cursor.execute("BEGIN IMMEDIATE")
                yield cursor
            except BaseException as exc:
                cursor.execute("ROLLBACK")
                if isinstance(exc, sqlite3.Error):
                    raise JournalError(f"journal write failed: {exc}") from exc
                raise
            else:
                try:
                    cursor.execute("COMMIT")
                except sqlite3.Error as exc:
                    raise JournalError(f"journal commit failed: {exc}") from exc

    def _require_open(self) -> None:
        if self._closed:
            raise JournalError("journal is closed")


def _decode_operation(row: sqlite3.Row) -> OperationRecord:
    return OperationRecord(
        operation_id=str(row["operation_id"]),
        action=OperationAction(row["action"]),
        fingerprint=str(row["fingerprint"]),
        state=OperationState(row["state"]),
        generation=int(row["generation"]),
        created_at=_decode_time(row["created_at"]),
        deadline_at=_decode_time(row["deadline_at"]),
        actor=Actor(user_id=str(row["actor_user_id"]), role=str(row["actor_role"])),
        trusted_terminal=bool(row["trusted_terminal"]),
        outcome=None if row["outcome"] is None else str(row["outcome"]),
        terminal_at=_decode_optional_time(row["terminal_at"]),
        protocol_request_id=(
            None if row["protocol_request_id"] is None else int(row["protocol_request_id"])
        ),
        terminal_fields=_decode_fields(row["terminal_fields"]),
    )


def _decode_transition(row: sqlite3.Row) -> TransitionRecord:
    return TransitionRecord(
        transition_id=int(row["transition_id"]),
        operation_id=str(row["operation_id"]),
        from_state=None if row["from_state"] is None else OperationState(row["from_state"]),
        to_state=OperationState(row["to_state"]),
        generation=int(row["generation"]),
        occurred_at=_decode_time(row["occurred_at"]),
        reason=str(row["reason"]),
    )


def _decode_idempotency(row: sqlite3.Row) -> IdempotencyRecord:
    return IdempotencyRecord(
        key=str(row["key"]),
        fingerprint=str(row["fingerprint"]),
        operation_id=str(row["operation_id"]),
        created_at=_decode_time(row["created_at"]),
        expires_at=_decode_time(row["expires_at"]),
    )


def _require_moment(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValidationError("journal timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _encode_time(value: datetime) -> str:
    return _require_moment(value).isoformat()


def _encode_optional_time(value: datetime | None) -> str | None:
    return None if value is None else _encode_time(value)


def _decode_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value)).astimezone(UTC)


def _decode_optional_time(value: str | None) -> datetime | None:
    return None if value is None else _decode_time(value)


def _encode_fields(fields: Mapping[str, str] | None) -> str | None:
    return None if fields is None else json.dumps(dict(fields), sort_keys=True)


def _decode_fields(value: str | None) -> Mapping[str, str] | None:
    if value is None:
        return None
    decoded = json.loads(str(value))
    if not isinstance(decoded, dict):  # pragma: no cover - written by this module only
        raise JournalError("stored terminal fields are not a mapping")
    return {str(name): str(field) for name, field in decoded.items()}


def _utcnow() -> datetime:
    return datetime.now(UTC)

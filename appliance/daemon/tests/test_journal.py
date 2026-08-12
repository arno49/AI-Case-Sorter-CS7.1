from __future__ import annotations

import ast
import sqlite3
import sys
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cs71d.journal import (
    IN_MEMORY,
    MIGRATIONS,
    SCHEMA_VERSION,
    Journal,
    JournalError,
    JournalSchemaError,
)
from cs71d.operations import (
    Actor,
    IdempotencyRecord,
    InvalidTransitionError,
    OperationAction,
    OperationRecord,
    OperationState,
    ValidationError,
    new_operation_id,
    request_fingerprint,
)

CREATED = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
DEADLINE = CREATED + timedelta(seconds=30)
EXPIRES = CREATED + timedelta(hours=24)
OPERATOR = Actor(user_id="opaque-bff-attribution", role="operator")
FINGERPRINT = request_fingerprint(OperationAction.SORT, {"slot": 3}, OPERATOR)


@pytest.fixture
def journal() -> Iterator[Journal]:
    with Journal.open(IN_MEMORY, now=lambda: CREATED) as opened:
        yield opened


@contextmanager
def _raw(database: Path) -> Iterator[sqlite3.Connection]:
    """Reach the storage engine directly, bypassing every Python-side guard."""
    connection = sqlite3.connect(database)
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def _operation(
    *,
    operation_id: str | None = None,
    generation: int = 7,
    fingerprint: str = FINGERPRINT,
) -> OperationRecord:
    return OperationRecord(
        operation_id=new_operation_id() if operation_id is None else operation_id,
        action=OperationAction.SORT,
        fingerprint=fingerprint,
        state=OperationState.QUEUED,
        generation=generation,
        created_at=CREATED,
        deadline_at=DEADLINE,
        actor=OPERATOR,
    )


def _admit(journal: Journal, operation: OperationRecord) -> OperationRecord:
    journal.record_admission(operation, reason="admitted for test")
    return operation


def _run_to_terminal(
    journal: Journal,
    operation: OperationRecord,
    *,
    to_state: OperationState,
    trusted_terminal: bool = False,
    terminal_fields: Mapping[str, str] | None = None,
) -> OperationRecord:
    journal.record_transition(
        operation.operation_id,
        to_state=OperationState.ACCEPTED,
        generation=operation.generation + 1,
        occurred_at=CREATED + timedelta(milliseconds=1),
        reason="admitted to the serial lane",
    )
    journal.record_transition(
        operation.operation_id,
        to_state=OperationState.RUNNING,
        generation=operation.generation + 2,
        occurred_at=CREATED + timedelta(milliseconds=2),
        reason="dispatched to the controller",
        protocol_request_id=41,
    )
    return journal.record_transition(
        operation.operation_id,
        to_state=to_state,
        generation=operation.generation + 3,
        occurred_at=CREATED + timedelta(milliseconds=3),
        reason=f"controller reported {to_state}",
        trusted_terminal=trusted_terminal,
        outcome="done",
        terminal_fields=terminal_fields,
    )


def test_sql_access_is_confined_to_the_journal() -> None:
    """No other daemon module may reach `machine.db` or speak SQL."""
    package_root = Path(__file__).parents[1] / "src/cs71d"
    importers: set[str] = set()
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            if "sqlite3" in names:
                importers.add(str(path.relative_to(package_root)))

    assert importers == {"journal.py"}


def test_open_applies_every_migration_once(tmp_path: Path) -> None:
    database = tmp_path / "machine.db"

    with Journal.open(database, now=lambda: CREATED) as first:
        assert first.schema_version == SCHEMA_VERSION
    with Journal.open(database, now=lambda: CREATED) as second:
        assert second.schema_version == SCHEMA_VERSION

    with _raw(database) as raw:
        applied = raw.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]
    assert applied == len(MIGRATIONS)


def test_a_file_journal_uses_wal_and_owner_only_permissions(tmp_path: Path) -> None:
    database = tmp_path / "machine.db"

    with Journal.open(database, now=lambda: CREATED), _raw(database) as raw:
        mode = raw.execute("PRAGMA journal_mode").fetchone()[0]

    assert str(mode).lower() == "wal"
    if sys.platform != "win32":
        assert database.stat().st_mode & 0o777 == 0o600


def test_a_missing_directory_is_reported_rather_than_created(tmp_path: Path) -> None:
    # Packaging and systemd own /var/lib/cs71d with its ownership and mode; the
    # daemon must not invent that directory with whatever umask it happens to have.
    with pytest.raises(JournalError, match="cannot open journal"):
        Journal.open(tmp_path / "absent" / "machine.db", now=lambda: CREATED)


def test_a_diverged_migration_definition_refuses_to_open(tmp_path: Path) -> None:
    database = tmp_path / "machine.db"
    Journal.open(database, now=lambda: CREATED).close()

    with _raw(database) as raw:
        raw.execute("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")

    with pytest.raises(JournalSchemaError, match="diverged"):
        Journal.open(database, now=lambda: CREATED)


def test_a_newer_schema_refuses_to_open_rather_than_downgrading(tmp_path: Path) -> None:
    database = tmp_path / "machine.db"
    Journal.open(database, now=lambda: CREATED).close()

    with _raw(database) as raw:
        raw.execute(
            "INSERT INTO schema_migrations (version, name, applied_at, checksum)"
            " VALUES (?, ?, ?, ?)",
            (SCHEMA_VERSION + 1, "from-the-future", CREATED.isoformat(), "checksum"),
        )

    with pytest.raises(JournalSchemaError, match="forward-only"):
        Journal.open(database, now=lambda: CREATED)


def test_admission_records_the_operation_and_its_first_transition(journal: Journal) -> None:
    operation = _admit(journal, _operation())

    stored = journal.operation(operation.operation_id)
    transitions = journal.transitions(operation.operation_id)

    assert stored == operation
    assert [(entry.from_state, entry.to_state) for entry in transitions] == [
        (None, OperationState.QUEUED)
    ]
    assert transitions[0].generation == operation.generation
    assert transitions[0].occurred_at == CREATED


def test_admission_requires_a_queued_operation(journal: Journal) -> None:
    running = OperationRecord(
        operation_id=new_operation_id(),
        action=OperationAction.HOME,
        fingerprint=FINGERPRINT,
        state=OperationState.RUNNING,
        generation=7,
        created_at=CREATED,
        deadline_at=DEADLINE,
        actor=OPERATOR,
    )

    with pytest.raises(InvalidTransitionError, match="must start queued"):
        journal.record_admission(running, reason="never durable")

    assert journal.operation(running.operation_id) is None


def test_admission_binds_the_idempotency_key_in_the_same_transaction(journal: Journal) -> None:
    operation = _operation()
    record = IdempotencyRecord(
        key="key-1",
        fingerprint=operation.fingerprint,
        operation_id=operation.operation_id,
        created_at=CREATED,
        expires_at=EXPIRES,
    )

    journal.record_admission(operation, reason="admitted with a key", idempotency=record)

    assert journal.idempotency_record("key-1", at=CREATED) == record
    assert journal.operation(operation.operation_id) is not None


def test_an_idempotency_record_must_describe_its_own_operation(journal: Journal) -> None:
    operation = _operation()
    foreign = IdempotencyRecord(
        key="key-2",
        fingerprint=operation.fingerprint,
        operation_id=new_operation_id(),
        created_at=CREATED,
        expires_at=EXPIRES,
    )

    with pytest.raises(ValidationError, match="its own operation"):
        journal.record_admission(operation, reason="mismatched", idempotency=foreign)

    mismatched = IdempotencyRecord(
        key="key-2",
        fingerprint="0" * 64,
        operation_id=operation.operation_id,
        created_at=CREATED,
        expires_at=EXPIRES,
    )
    with pytest.raises(ValidationError, match="request fingerprint"):
        journal.record_admission(operation, reason="mismatched", idempotency=mismatched)

    assert journal.operation(operation.operation_id) is None


def test_a_reused_key_cannot_be_bound_twice(journal: Journal) -> None:
    first = _operation()
    journal.record_admission(
        first,
        reason="first",
        idempotency=IdempotencyRecord(
            key="key-3",
            fingerprint=first.fingerprint,
            operation_id=first.operation_id,
            created_at=CREATED,
            expires_at=EXPIRES,
        ),
    )
    second = _operation()

    with pytest.raises(JournalError):
        journal.record_admission(
            second,
            reason="second",
            idempotency=IdempotencyRecord(
                key="key-3",
                fingerprint=second.fingerprint,
                operation_id=second.operation_id,
                created_at=CREATED,
                expires_at=EXPIRES,
            ),
        )

    assert journal.operation(second.operation_id) is None


def test_expired_keys_are_invisible_and_prunable(journal: Journal) -> None:
    operation = _operation()
    journal.record_admission(
        operation,
        reason="admitted",
        idempotency=IdempotencyRecord(
            key="key-4",
            fingerprint=operation.fingerprint,
            operation_id=operation.operation_id,
            created_at=CREATED,
            expires_at=EXPIRES,
        ),
    )

    assert journal.idempotency_record("key-4", at=EXPIRES - timedelta(seconds=1)) is not None
    assert journal.idempotency_record("key-4", at=EXPIRES) is None
    assert journal.prune_idempotency(at=CREATED) == 0
    assert journal.prune_idempotency(at=EXPIRES) == 1
    assert journal.idempotency_record("key-4", at=CREATED) is None
    assert journal.operation(operation.operation_id) is not None


def test_a_trusted_terminal_completes_the_lifecycle_and_its_audit_trail(journal: Journal) -> None:
    operation = _admit(journal, _operation())

    final = _run_to_terminal(
        journal,
        operation,
        to_state=OperationState.SUCCEEDED,
        trusted_terminal=True,
    )

    assert final.state is OperationState.SUCCEEDED
    assert final.trusted_terminal
    assert final.terminal_at == CREATED + timedelta(milliseconds=3)
    assert final.generation == operation.generation + 3
    assert final.protocol_request_id == 41
    assert [entry.to_state for entry in journal.transitions(operation.operation_id)] == [
        OperationState.QUEUED,
        OperationState.ACCEPTED,
        OperationState.RUNNING,
        OperationState.SUCCEEDED,
    ]


def test_success_without_a_trusted_terminal_is_refused(journal: Journal) -> None:
    operation = _admit(journal, _operation())

    with pytest.raises(InvalidTransitionError, match="trusted correlated firmware terminal"):
        _run_to_terminal(journal, operation, to_state=OperationState.SUCCEEDED)

    stored = journal.operation(operation.operation_id)
    assert stored is not None
    assert stored.state is OperationState.RUNNING


def test_the_database_itself_refuses_untrusted_success(tmp_path: Path) -> None:
    database = tmp_path / "machine.db"
    with Journal.open(database, now=lambda: CREATED) as opened:
        operation = _admit(opened, _operation())

        with (
            pytest.raises(sqlite3.IntegrityError, match="trusted firmware terminal"),
            _raw(database) as raw,
        ):
            raw.execute(
                "UPDATE operations SET state = 'succeeded' WHERE operation_id = ?",
                (operation.operation_id,),
            )

        stored = opened.operation(operation.operation_id)
        assert stored is not None
        assert stored.state is OperationState.QUEUED


def test_the_audit_trail_is_append_only(tmp_path: Path) -> None:
    database = tmp_path / "machine.db"
    with Journal.open(database, now=lambda: CREATED) as opened:
        operation = _admit(opened, _operation())

        for statement in (
            "UPDATE operation_transitions SET reason = 'rewritten'",
            "DELETE FROM operation_transitions",
        ):
            with (
                pytest.raises(sqlite3.IntegrityError, match="append-only"),
                _raw(database) as raw,
            ):
                raw.execute(statement)

        assert len(opened.transitions(operation.operation_id)) == 1


@pytest.mark.parametrize(
    "to_state",
    [OperationState.SUCCEEDED, OperationState.RUNNING],
)
def test_a_queued_operation_cannot_skip_dispatch(
    journal: Journal,
    to_state: OperationState,
) -> None:
    operation = _admit(journal, _operation())

    with pytest.raises(InvalidTransitionError):
        journal.record_transition(
            operation.operation_id,
            to_state=to_state,
            generation=operation.generation + 1,
            occurred_at=CREATED,
            reason="skipping dispatch",
            trusted_terminal=to_state is OperationState.SUCCEEDED,
        )


def test_a_terminal_operation_cannot_transition_again(journal: Journal) -> None:
    operation = _admit(journal, _operation())
    journal.record_transition(
        operation.operation_id,
        to_state=OperationState.CANCELLED,
        generation=operation.generation + 1,
        occurred_at=CREATED,
        reason="preempted by priority stop",
    )

    with pytest.raises(InvalidTransitionError, match="cannot move"):
        journal.record_transition(
            operation.operation_id,
            to_state=OperationState.ACCEPTED,
            generation=operation.generation + 2,
            occurred_at=CREATED,
            reason="resurrection",
        )


def test_generation_never_moves_backwards(journal: Journal) -> None:
    operation = _admit(journal, _operation(generation=7))

    with pytest.raises(InvalidTransitionError, match="backwards"):
        journal.record_transition(
            operation.operation_id,
            to_state=OperationState.ACCEPTED,
            generation=6,
            occurred_at=CREATED,
            reason="stale generation",
        )


def test_transitions_for_an_unknown_operation_are_refused(journal: Journal) -> None:
    with pytest.raises(JournalError, match="unknown operation"):
        journal.record_transition(
            new_operation_id(),
            to_state=OperationState.ACCEPTED,
            generation=1,
            occurred_at=CREATED,
            reason="never admitted",
        )


def test_a_closed_journal_refuses_reads_and_writes(tmp_path: Path) -> None:
    journal = Journal.open(tmp_path / "machine.db", now=lambda: CREATED)
    journal.close()
    journal.close()

    with pytest.raises(JournalError, match="closed"):
        journal.operation(new_operation_id())


def test_admission_and_transitions_are_safe_from_several_threads(journal: Journal) -> None:
    operations = [_operation() for _ in range(8)]
    failures: list[BaseException] = []

    def run(operation: OperationRecord) -> None:
        try:
            _admit(journal, operation)
            journal.record_transition(
                operation.operation_id,
                to_state=OperationState.ACCEPTED,
                generation=operation.generation + 1,
                occurred_at=CREATED,
                reason="admitted to the serial lane",
            )
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread
            failures.append(exc)

    threads = [threading.Thread(target=run, args=(operation,)) for operation in operations]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)

    assert failures == []
    assert all(
        journal.operation(operation.operation_id) is not None
        and journal.transitions(operation.operation_id)[-1].to_state is OperationState.ACCEPTED
        for operation in operations
    )


def test_an_existing_database_migrates_forward_without_losing_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reopening an older schema applies the pending migration in place."""
    database = tmp_path / "machine.db"
    monkeypatch.setattr("cs71d.journal.MIGRATIONS", MIGRATIONS[:1])
    with Journal.open(database, now=lambda: CREATED) as older:
        assert older.schema_version == 1
    monkeypatch.undo()

    # Written the way the older release would have, without the newer column.
    operation_id = new_operation_id()
    with _raw(database) as raw:
        raw.execute(
            "INSERT INTO operations (operation_id, action, fingerprint, state, generation,"
            " created_at, deadline_at, actor_user_id, actor_role)"
            " VALUES (?, 'sort', ?, 'queued', 7, ?, ?, ?, ?)",
            (
                operation_id,
                FINGERPRINT,
                CREATED.isoformat(),
                DEADLINE.isoformat(),
                OPERATOR.user_id,
                OPERATOR.role,
            ),
        )
        raw.execute(
            "INSERT INTO operation_transitions"
            " (operation_id, from_state, to_state, generation, occurred_at, reason)"
            " VALUES (?, NULL, 'queued', 7, ?, 'admitted by an older release')",
            (operation_id, CREATED.isoformat()),
        )

    with Journal.open(database, now=lambda: CREATED) as upgraded:
        assert upgraded.schema_version == SCHEMA_VERSION
        carried = upgraded.operation(operation_id)
        assert carried is not None
        assert carried.state is OperationState.QUEUED
        assert carried.terminal_fields is None
        assert len(upgraded.transitions(operation_id)) == 1


def test_a_terminal_records_the_fields_the_controller_reported(journal: Journal) -> None:
    operation = _admit(journal, _operation())

    final = _run_to_terminal(
        journal,
        operation,
        to_state=OperationState.SUCCEEDED,
        trusted_terminal=True,
        terminal_fields={"slot": "3", "elapsed_ms": "25"},
    )

    assert final.terminal_fields == {"slot": "3", "elapsed_ms": "25"}
    reread = journal.operation(operation.operation_id)
    assert reread is not None
    assert reread.terminal_fields == {"slot": "3", "elapsed_ms": "25"}


def test_unusable_terminal_fields_are_refused(journal: Journal) -> None:
    operation = _admit(journal, _operation())

    with pytest.raises(ValidationError):
        _run_to_terminal(
            journal,
            operation,
            to_state=OperationState.SUCCEEDED,
            trusted_terminal=True,
            terminal_fields={"Slot": "3"},
        )


def test_the_applied_configuration_is_the_newest_version(journal: Journal) -> None:
    assert journal.applied_configuration() is None

    for generation, interval in ((7, 15_000), (9, 20_000)):
        journal.record_configuration(
            config_id=new_operation_id(),
            generation=generation,
            values={"heartbeat_interval_ms": interval},
            source="administrator:opaque",
            created_at=CREATED,
        )

    applied = journal.applied_configuration()
    assert applied is not None
    values, generation, created_at = applied
    assert values == {"heartbeat_interval_ms": 20_000}
    assert generation == 9
    assert created_at == CREATED

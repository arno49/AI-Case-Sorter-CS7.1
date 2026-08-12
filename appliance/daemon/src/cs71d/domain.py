"""Operation admission, dispatch and terminal outcome.

This is where a caller's request becomes a durable operation. Admission
evaluates the idempotency key, the observed snapshot generation and machine
readiness atomically, and makes the operation durable *before* anything is
enqueued for the serial worker, so a stale or duplicate request cannot reach
the controller.

The layering is deliberate and one-directional:

``OperationDomain`` -> ``MachineState`` -> ``Journal`` -> ``SerialWorker``

Locks are taken in that order and never the other way. The worker thread
publishes its transitions while holding nothing, so it can always enter the
machine lock; an admitting thread therefore never holds the machine lock while
waiting on the worker.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cs71_protocol import Completion, ParseError, ResponseKind, Status

from .adapters import intent_for, require_supported
from .configuration import ConfigurationChanges, ConfigurationValues
from .journal import Journal, JournalError
from .machine import FirmwareProfile, MachineReadiness, MachineSnapshot, MachineState
from .operations import (
    Actor,
    DomainError,
    IdempotencyRecord,
    NotReadyError,
    OperationAction,
    OperationRecord,
    OperationState,
    RequestBody,
    new_operation_id,
    request_fingerprint,
    require_idempotency_key,
)
from .serial_worker import (
    PreemptedByRecoveryError,
    PreemptedByStopError,
    QueueFullError,
    SerialWorker,
    SerialWorkerError,
    SessionAction,
    SessionIntent,
    SessionNotReadyError,
    SessionProfile,
    StopInProgressError,
    WorkerNotRunningError,
    WorkerResult,
    WorkerUncertainError,
)
from .session import SessionSnapshot

MIN_DEADLINE_MS = 100
MAX_DEADLINE_MS = 600_000
DEFAULT_IDEMPOTENCY_TTL = timedelta(hours=24)


class StaleGenerationError(DomainError):
    """The caller acted on a machine view that has since moved."""

    code = "STALE_GENERATION"


class IdempotencyConflictError(DomainError):
    """One idempotency key was reused for a different canonical request."""

    code = "IDEMPOTENCY_CONFLICT"


class OperationQueueFullError(DomainError):
    """Bounded admission rejected the request; retry needs user intent."""

    code = "QUEUE_FULL"


class DeadlineInvalidError(DomainError):
    """The requested deadline is missing or outside daemon policy."""

    code = "DEADLINE_INVALID"


class DeadlineExpiredError(DomainError):
    """The finite deadline passed before the command could be transmitted."""

    code = "DEADLINE_EXPIRED"


class JournalUnavailableError(DomainError):
    """The daemon cannot durably record what it is being asked to do."""

    code = "JOURNAL_UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class WorkerObservers:
    """The callbacks a worker must publish into the machine view."""

    session: Callable[[SessionSnapshot], None]
    profile: Callable[[SessionProfile], None]


type WorkerFactory = Callable[[WorkerObservers], SerialWorker]


class OperationDomain:
    """Admit, track and durably resolve state-changing machine operations."""

    def __init__(
        self,
        journal: Journal,
        worker_factory: WorkerFactory,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        idempotency_ttl: timedelta = DEFAULT_IDEMPOTENCY_TTL,
        min_deadline_ms: int = MIN_DEADLINE_MS,
        max_deadline_ms: int = MAX_DEADLINE_MS,
        operation_observer: Callable[[OperationRecord], None] | None = None,
        machine_observer: Callable[[MachineSnapshot], None] | None = None,
    ) -> None:
        if idempotency_ttl <= timedelta(0):
            raise ValueError("idempotency_ttl must be positive")
        if not 0 < min_deadline_ms <= max_deadline_ms:
            raise ValueError("deadline policy must be a positive ascending range")
        self._journal = journal
        self._machine = MachineState(observer=machine_observer)
        self._now = now
        self._idempotency_ttl = idempotency_ttl
        self._min_deadline_ms = min_deadline_ms
        self._max_deadline_ms = max_deadline_ms
        # Reports every lifecycle transition, on whichever thread caused it.
        # It must not call back into the domain.
        self._operation_observer = operation_observer
        self._configuration = ConfigurationValues()
        self._configuration_generation = 1
        self._configured_at = now()
        # The worker publishes connection state into the machine view, so it
        # must be constructed with the observer already wired.
        self._worker = worker_factory(
            WorkerObservers(session=self._observe_session, profile=self._observe_profile)
        )

    @property
    def worker(self) -> SerialWorker:
        return self._worker

    @property
    def machine(self) -> MachineState:
        return self._machine

    @property
    def snapshot(self) -> MachineSnapshot:
        return self._machine.snapshot

    def start(self, *, timeout: float = 5.0) -> None:
        self._worker.start(timeout=timeout)

    def close(self, *, timeout: float = 5.0) -> None:
        """Stop the worker. The caller owns the journal and closes it."""
        self._worker.close(timeout=timeout)

    def operation(self, operation_id: str) -> OperationRecord | None:
        return self._journal.operation(operation_id)

    def _observe_session(self, session: SessionSnapshot) -> None:
        """Fold session confidence into the machine view, on the worker thread."""
        self._machine.observe_connection(session)

    def _observe_profile(self, profile: SessionProfile) -> None:
        """Translate observed protocol snapshots into the machine view."""
        capabilities = profile.capabilities
        status = profile.status
        self._machine.observe_profile(
            FirmwareProfile(
                protocol_version=capabilities.protocol,
                slot_max=capabilities.slot_max,
                slot_count=capabilities.slot_count,
                queue_depth=capabilities.queue_depth,
                feed_sensor=capabilities.feed_sensor,
                feed_home=capabilities.feed_home,
                sort_home=capabilities.sort_home,
            ),
            MachineReadiness(
                feed_homed=status.feed_homed,
                sort_homed=status.sort_homed,
                fault_code=status.fault_code,
                mode=status.mode,
                phase=status.phase,
            ),
        )

    def submit(
        self,
        action: OperationAction,
        body: RequestBody,
        *,
        actor: Actor,
        idempotency_key: str,
        expected_generation: int,
        deadline_ms: int,
    ) -> OperationRecord:
        """Admit one state-changing request and return its durable operation.

        The returned record is the operation as admitted, not its outcome. A
        replayed idempotency key returns the original operation, which may
        already be terminal.
        """
        intent = intent_for(action, body)
        fingerprint = request_fingerprint(action, body, actor)
        key = require_idempotency_key(idempotency_key)
        window = self._require_deadline(deadline_ms)
        now = self._now()

        with self._machine.admission() as view:
            self._require_durable(view)
            with self._durable("admitting an operation"):
                replayed = self._replay(key, fingerprint, at=now)
                if replayed is not None:
                    return replayed
                if expected_generation != view.generation:
                    raise StaleGenerationError(
                        f"request observed generation {expected_generation};"
                        f" the machine is at {view.generation}"
                    )
                if not view.admits_work:
                    raise NotReadyError(f"machine does not admit work: {view.connection}")
                # Capability, gate and readiness checks run against the frozen
                # view, before the operation exists and before any enqueue.
                require_supported(intent, view)

                accepted = self._admit(
                    action,
                    fingerprint,
                    actor=actor,
                    key=key,
                    view=view,
                    now=now,
                    window=window,
                    reason="admitted to the serial lane",
                )

        # Outside the machine lock on purpose: the worker thread enters that
        # lock to publish its own transitions, so holding it across an enqueue
        # would invert the lock order. Every admission check already ran, and
        # the operation is already durable.
        try:
            future = self._worker.submit(intent, on_dispatch=lambda: self._dispatch(accepted))
        except SerialWorkerError as exc:
            self._resolve(accepted.operation_id, exc)
            raise _translate(exc, accepted.operation_id) from exc
        future.add_done_callback(lambda done: self._settle(accepted.operation_id, done))
        return accepted

    def stop(
        self,
        *,
        actor: Actor,
        idempotency_key: str,
        expected_generation: int | None,
        deadline_ms: int,
    ) -> OperationRecord:
        """Admit an attributable priority software stop.

        Stop is admitted whether or not the machine admits ordinary work: a
        session that is recovering or uncertain is exactly when an operator
        needs it. ``expected_generation`` may be ``None``, the API's ``*``,
        to request the attempt despite a stale or uncertain view; the outcome
        is still trusted, failed or `UNCERTAIN` and never assumed.

        This is a **software stop, not an emergency stop**. It is refused when
        it cannot be recorded, because an unattributable stop is exactly the
        kind of claim this daemon must not make; the physical E-stop is the
        safety device and is independent of this path.
        """
        fingerprint = request_fingerprint(OperationAction.STOP, {}, actor)
        key = require_idempotency_key(idempotency_key)
        window = self._require_deadline(deadline_ms)
        now = self._now()

        with self._machine.admission() as view:
            self._require_durable(view)
            with self._durable("admitting a priority stop"):
                replayed = self._replay(key, fingerprint, at=now)
                if replayed is not None:
                    return replayed
                if expected_generation is not None and expected_generation != view.generation:
                    raise StaleGenerationError(
                        f"stop observed generation {expected_generation};"
                        f" the machine is at {view.generation}"
                    )
                accepted = self._admit(
                    OperationAction.STOP,
                    fingerprint,
                    actor=actor,
                    key=key,
                    view=view,
                    now=now,
                    window=window,
                    reason="admitted to the priority stop lane",
                )
                # The priority lane is not queued, so admission and dispatch
                # coincide; recording RUNNING before handing it over also keeps
                # the terminal callback from racing this transition.
                running = self._transition(
                    accepted.operation_id,
                    OperationState.RUNNING,
                    "handed to the priority stop lane",
                )

        try:
            future = self._worker.submit_priority_stop()
        except SerialWorkerError as exc:
            self._resolve(running.operation_id, exc)
            raise _translate(exc, running.operation_id) from exc
        future.add_done_callback(lambda done: self._settle_stop(running.operation_id, done))
        return running

    def connect(
        self,
        *,
        actor: Actor,
        idempotency_key: str,
        expected_generation: int,
        deadline_ms: int,
    ) -> OperationRecord:
        """Admit a durable operation that establishes a verified session."""
        return self._session_operation(
            OperationAction.CONNECT,
            SessionAction.CONNECT,
            actor=actor,
            idempotency_key=idempotency_key,
            expected_generation=expected_generation,
            deadline_ms=deadline_ms,
        )

    def recover(
        self,
        *,
        actor: Actor,
        idempotency_key: str,
        expected_generation: int,
        deadline_ms: int,
    ) -> OperationRecord:
        """Admit a durable operation that replaces the session from scratch.

        Recovery is never implicit at this level: the caller has confirmed that
        it wants the current session torn down, which is why it always starts
        again from a fresh transport rather than trusting what it has.
        """
        return self._session_operation(
            OperationAction.RECOVER,
            SessionAction.RECOVER,
            actor=actor,
            idempotency_key=idempotency_key,
            expected_generation=expected_generation,
            deadline_ms=deadline_ms,
        )

    def _session_operation(
        self,
        action: OperationAction,
        session_action: SessionAction,
        *,
        actor: Actor,
        idempotency_key: str,
        expected_generation: int,
        deadline_ms: int,
    ) -> OperationRecord:
        fingerprint = request_fingerprint(action, {}, actor)
        key = require_idempotency_key(idempotency_key)
        window = self._require_deadline(deadline_ms)
        now = self._now()

        with self._machine.admission() as view:
            self._require_durable(view)
            with self._durable(f"admitting a {action} operation"):
                replayed = self._replay(key, fingerprint, at=now)
                if replayed is not None:
                    return replayed
                if expected_generation != view.generation:
                    raise StaleGenerationError(
                        f"request observed generation {expected_generation};"
                        f" the machine is at {view.generation}"
                    )
                # Readiness is deliberately not required: repairing an unready
                # session is the whole purpose of these two operations.
                accepted = self._admit(
                    action,
                    fingerprint,
                    actor=actor,
                    key=key,
                    view=view,
                    now=now,
                    window=window,
                    reason=f"admitted to the serial lane as a {action} operation",
                )

        try:
            future = self._worker.submit(
                SessionIntent(session_action),
                on_dispatch=lambda: self._dispatch(accepted),
            )
        except SerialWorkerError as exc:
            self._resolve(accepted.operation_id, exc)
            raise _translate(exc, accepted.operation_id) from exc
        future.add_done_callback(lambda done: self._settle(accepted.operation_id, done))
        return accepted

    def configuration(self) -> tuple[ConfigurationValues, int, datetime]:
        """Return the applied policy, the generation that applied it, and when."""
        applied = self._journal.applied_configuration()
        if applied is None:
            return self._configuration, self._configuration_generation, self._configured_at
        values, generation, created_at = applied
        return ConfigurationValues.from_mapping(values), generation, created_at

    def configure(
        self,
        changes: ConfigurationChanges,
        *,
        actor: Actor,
        idempotency_key: str,
        expected_generation: int,
        deadline_ms: int,
    ) -> OperationRecord:
        """Apply a validated daemon policy change as a durable operation.

        Configuration never reaches the controller, so this admits while the
        session is unready. It still requires durability: an appliance that
        cannot record when a policy changed cannot explain its own behaviour.
        """
        key = require_idempotency_key(idempotency_key)
        window = self._require_deadline(deadline_ms)
        now = self._now()
        current, _generation, _at = self.configuration()
        candidate = current.with_changes(changes)
        fingerprint = request_fingerprint(
            OperationAction.CONFIGURE, dict(sorted(changes.items())), actor
        )

        with self._machine.admission() as view:
            self._require_durable(view)
            with self._durable("applying a configuration change"):
                replayed = self._replay(key, fingerprint, at=now)
                if replayed is not None:
                    return replayed
                if expected_generation != view.generation:
                    raise StaleGenerationError(
                        f"request observed generation {expected_generation};"
                        f" the machine is at {view.generation}"
                    )
                accepted = self._admit(
                    OperationAction.CONFIGURE,
                    fingerprint,
                    actor=actor,
                    key=key,
                    view=view,
                    now=now,
                    window=window,
                    reason="admitted as a daemon configuration change",
                )
                running = self._transition(
                    accepted.operation_id,
                    OperationState.RUNNING,
                    "applying the configuration change",
                )
                applied = self._apply_configuration(running, candidate, actor, now)
        return applied

    def _apply_configuration(
        self,
        operation: OperationRecord,
        values: ConfigurationValues,
        actor: Actor,
        now: datetime,
    ) -> OperationRecord:
        """Commit the new values, then record the operation as successful.

        A configuration change has no firmware terminal, and it must not claim
        one. Its trusted terminal is the committed configuration snapshot: the
        daemon's own durable record that the values are in force. The order
        matters — the snapshot is written first, so a success can never be
        recorded for values that were not stored.
        """
        with self._machine.transition(operation.operation_id, "configuration applied") as version:
            self._journal.record_configuration(
                config_id=new_operation_id(),
                generation=version,
                values=values.as_mapping(),
                source=f"{actor.role}:{actor.user_id}",
                created_at=now,
            )
        self._configuration = values
        self._configuration_generation = version
        self._configured_at = now
        self._max_deadline_ms = min(self._max_deadline_ms, values.max_deadline_ms)
        return self._transition(
            operation.operation_id,
            OperationState.SUCCEEDED,
            "configuration committed",
            trusted_terminal=True,
            outcome="applied",
            terminal_fields={name: str(value) for name, value in values.as_mapping().items()},
        )

    def _admit(
        self,
        action: OperationAction,
        fingerprint: str,
        *,
        actor: Actor,
        key: str,
        view: MachineSnapshot,
        now: datetime,
        window: timedelta,
        reason: str,
    ) -> OperationRecord:
        operation = OperationRecord(
            operation_id=new_operation_id(),
            action=action,
            fingerprint=fingerprint,
            state=OperationState.QUEUED,
            generation=view.generation,
            created_at=now,
            deadline_at=now + window,
            actor=actor,
        )
        self._journal.record_admission(
            operation,
            reason=f"admitted against generation {view.generation}",
            idempotency=IdempotencyRecord(
                key=key,
                fingerprint=fingerprint,
                operation_id=operation.operation_id,
                created_at=now,
                expires_at=now + self._idempotency_ttl,
            ),
        )
        return self._transition(operation.operation_id, OperationState.ACCEPTED, reason)

    def _record_journal_fault(self, reason: str) -> None:
        """Latch durability loss with an identity the API can report."""
        self._machine.record_journal_fault(
            reason,
            fault_id=new_operation_id(),
            opened_at=self._now(),
        )

    def _require_durable(self, view: MachineSnapshot) -> None:
        if not view.journal_available:
            raise JournalUnavailableError(
                "the operation journal is unavailable; new work is blocked until"
                " durability is restored"
            )

    @contextmanager
    def _durable(self, activity: str) -> Iterator[None]:
        """Turn a failed journal write into a latched, visible daemon fault."""
        try:
            yield
        except JournalError as exc:
            self._record_journal_fault(f"journal failure while {activity}: {exc}")
            raise JournalUnavailableError(f"could not record while {activity}: {exc}") from exc

    def _replay(self, key: str, fingerprint: str, *, at: datetime) -> OperationRecord | None:
        existing = self._journal.idempotency_record(key, at=at)
        if existing is None:
            return None
        if existing.fingerprint != fingerprint:
            raise IdempotencyConflictError(
                f"idempotency key {key} is already bound to a different canonical request"
            )
        record = self._journal.operation(existing.operation_id)
        if record is None:  # pragma: no cover - written in one transaction
            raise JournalError(f"idempotency key {key} points at a missing operation")
        return record

    def _dispatch(self, operation: OperationRecord) -> None:
        """Gate transmission on the worker thread, immediately before the write.

        A deadline that expired while the operation waited in the queue fails
        it here, which is the last moment the command can still be stopped
        from reaching the controller. Raising also covers a failed journal
        write: the daemon does not transmit what it cannot record.
        """
        if operation.expired_at(self._now()):
            raise DeadlineExpiredError(
                f"deadline expired before operation {operation.operation_id} was transmitted"
            )
        self._transition(
            operation.operation_id,
            OperationState.RUNNING,
            "dispatched to the controller",
        )

    def _settle(self, operation_id: str, future: Future[WorkerResult]) -> None:
        try:
            result = future.result()
        except Exception as exc:
            self._resolve(operation_id, exc)
            return
        self._complete(operation_id, result)

    def _settle_stop(self, operation_id: str, future: Future[None]) -> None:
        """Resolve the stop operation from the worker's stop lane.

        The trusted terminal for a stop is the exact ID-less ``stopped`` line
        the protocol library waits for. Anything else — timeout, transport
        loss, failed recovery — leaves the stop `UNCERTAIN`. It is never
        reported as stopped-successful on the strength of having asked.
        """
        try:
            future.result()
        except Exception as exc:
            self._resolve(operation_id, exc)
            return
        self._safe_transition(
            operation_id,
            OperationState.SUCCEEDED,
            "trusted exact stopped terminal",
            trusted_terminal=True,
            outcome="stopped",
        )

    def _complete(self, operation_id: str, result: WorkerResult) -> None:
        record = self._current(operation_id)
        if record is None or record.is_terminal:
            return
        if isinstance(result, Status) and record.action in _SESSION_ACTIONS:
            # A session operation has no command terminal of its own. It is
            # successful once the session is verified and its required
            # snapshots exist, which this status is read from.
            self._safe_transition(
                operation_id,
                OperationState.SUCCEEDED,
                "session verified and required snapshots gathered",
                trusted_terminal=True,
                outcome="ready",
                terminal_fields={"mode": result.mode, "phase": result.phase},
            )
            return
        if not isinstance(result, Completion):  # pragma: no cover - queries own no operation
            self._safe_transition(
                operation_id,
                OperationState.UNCERTAIN,
                f"worker returned a non-command result: {type(result).__name__}",
                outcome="UNEXPECTED_RESULT",
            )
            return
        if _is_trusted_terminal(result):
            self._safe_transition(
                operation_id,
                OperationState.SUCCEEDED,
                "trusted correlated firmware terminal",
                trusted_terminal=True,
                outcome="done",
                protocol_request_id=result.request_id,
                terminal_fields=result.terminal_fields,
            )
            return
        fault = result.error
        self._safe_transition(
            operation_id,
            OperationState.FAILED,
            "firmware reported a correlated error terminal",
            outcome="error" if fault is None else fault.name,
            protocol_request_id=result.request_id,
            terminal_fields=result.terminal_fields,
        )

    def _resolve(self, operation_id: str, error: Exception) -> None:
        record = self._current(operation_id)
        if record is None or record.is_terminal:
            return
        state, reason = _classify(record.state, error)
        self._safe_transition(operation_id, state, reason, outcome=_outcome_of(error))

    def _current(self, operation_id: str) -> OperationRecord | None:
        try:
            return self._journal.operation(operation_id)
        except JournalError as exc:
            self._record_journal_fault(f"journal read failed: {exc}")
            return None

    def _safe_transition(
        self,
        operation_id: str,
        state: OperationState,
        reason: str,
        *,
        trusted_terminal: bool = False,
        outcome: str | None = None,
        protocol_request_id: int | None = None,
        terminal_fields: Mapping[str, str] | None = None,
    ) -> None:
        """Record an outcome that has nowhere else to go.

        These run on the worker thread, after the command already happened, so
        there is no caller left to reject. A journal failure here latches the
        machine as undurable and the outcome stays unrecorded; the daemon never
        substitutes an in-memory claim of success for a durable record.
        """
        try:
            self._transition(
                operation_id,
                state,
                reason,
                trusted_terminal=trusted_terminal,
                outcome=outcome,
                protocol_request_id=protocol_request_id,
                terminal_fields=terminal_fields,
            )
        except JournalError:
            return

    def _transition(
        self,
        operation_id: str,
        state: OperationState,
        reason: str,
        *,
        trusted_terminal: bool = False,
        outcome: str | None = None,
        protocol_request_id: int | None = None,
        terminal_fields: Mapping[str, str] | None = None,
    ) -> OperationRecord:
        """Advance one operation and publish the generation it moved to.

        The durable write and the published generation happen under the same
        machine lock, so the version a caller observes is never ahead of the
        journal: if the write fails, the generation does not move either.
        """
        active = None if state in _SETTLED else operation_id
        try:
            record = self._write_transition(
                operation_id,
                state,
                reason,
                active=active,
                trusted_terminal=trusted_terminal,
                outcome=outcome,
                protocol_request_id=protocol_request_id,
                terminal_fields=terminal_fields,
            )
        except JournalError as exc:
            self._record_journal_fault(
                f"journal failure recording {state} for operation {operation_id}: {exc}"
            )
            raise
        if self._operation_observer is not None:
            self._operation_observer(record)
        return record

    def _write_transition(
        self,
        operation_id: str,
        state: OperationState,
        reason: str,
        *,
        active: str | None,
        trusted_terminal: bool,
        outcome: str | None,
        protocol_request_id: int | None,
        terminal_fields: Mapping[str, str] | None,
    ) -> OperationRecord:
        with self._machine.transition(active, f"operation {state}: {reason}") as generation:
            record = self._journal.record_transition(
                operation_id,
                to_state=state,
                generation=generation,
                occurred_at=self._now(),
                reason=reason,
                trusted_terminal=trusted_terminal,
                outcome=outcome,
                protocol_request_id=protocol_request_id,
                terminal_fields=terminal_fields,
            )
        return record

    def _require_deadline(self, deadline_ms: int) -> timedelta:
        if isinstance(deadline_ms, bool) or not isinstance(deadline_ms, int):
            raise DeadlineInvalidError("deadline must be an integer count of milliseconds")
        if not self._min_deadline_ms <= deadline_ms <= self._max_deadline_ms:
            raise DeadlineInvalidError(
                f"deadline must be between {self._min_deadline_ms} and"
                f" {self._max_deadline_ms} ms, got {deadline_ms}"
            )
        return timedelta(milliseconds=deadline_ms)


_SETTLED = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.UNCERTAIN,
    }
)

_SESSION_ACTIONS = frozenset({OperationAction.CONNECT, OperationAction.RECOVER})

_NEVER_DISPATCHED = (
    WorkerNotRunningError,
    SessionNotReadyError,
    QueueFullError,
    StopInProgressError,
)


def _is_trusted_terminal(completion: Completion) -> bool:
    """Report whether this completion is a trusted correlated firmware terminal."""
    try:
        terminal = completion.terminal_response
    except ParseError:
        return False
    return (
        completion.succeeded
        and terminal.kind is ResponseKind.DONE
        and terminal.request_id == completion.request_id
    )


def _classify(current: OperationState, error: Exception) -> tuple[OperationState, str]:
    """Map a failure to a fail-closed terminal state.

    Anything that reached the wire without a trusted terminal is `UNCERTAIN`,
    never failed: the daemon does not know whether the machine moved.
    """
    if isinstance(error, DeadlineExpiredError):
        return OperationState.FAILED, "deadline expired before transmission"
    if isinstance(error, PreemptedByStopError):
        return OperationState.CANCELLED, "invalidated by a trusted priority stop"
    if isinstance(error, PreemptedByRecoveryError):
        return OperationState.CANCELLED, "discarded by session recovery; never replayed"
    if isinstance(error, WorkerUncertainError):
        return OperationState.UNCERTAIN, f"worker could not establish a trusted outcome: {error}"
    if isinstance(error, _NEVER_DISPATCHED):
        return OperationState.CANCELLED, f"never dispatched: {error}"
    if current is not OperationState.RUNNING:
        return OperationState.FAILED, f"failed before transmission: {error}"
    return OperationState.UNCERTAIN, f"transmitted without a trusted terminal: {error}"


def _outcome_of(error: Exception) -> str:
    if isinstance(error, DomainError):
        return error.code
    return type(error).__name__


def _translate(error: SerialWorkerError, operation_id: str) -> DomainError:
    if isinstance(error, QueueFullError):
        return OperationQueueFullError(str(error), operation_id=operation_id)
    if isinstance(error, StopInProgressError):
        return NotReadyError(f"priority stop is in progress: {error}", operation_id=operation_id)
    return NotReadyError(str(error), operation_id=operation_id)

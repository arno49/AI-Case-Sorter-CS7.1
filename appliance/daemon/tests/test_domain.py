from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from cs71d.adapters import FEED_LIFECYCLE_GATE
from cs71d.domain import (
    DeadlineInvalidError,
    IdempotencyConflictError,
    JournalUnavailableError,
    OperationDomain,
    OperationQueueFullError,
    StaleGenerationError,
    WorkerObservers,
)
from cs71d.journal import IN_MEMORY, Journal, JournalError
from cs71d.machine import FaultState, MachineSnapshot
from cs71d.operations import (
    Actor,
    IdempotencyRecord,
    NotReadyError,
    OperationAction,
    OperationRecord,
    OperationState,
    UnsupportedOperationError,
    ValidationError,
)
from cs71d.serial_worker import SerialWorker
from cs71d.simulator import AdverseScenario, SimulatorConfig, SimulatorTransport
from cs71d.simulator.transport import TranscriptDirection

START = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
OPERATOR = Actor(user_id="opaque-bff-attribution", role="operator")
SORT_BODY: dict[str, Any] = {"slot": 3}
HOME_BODY: dict[str, Any] = {"axis": "both"}


class FakeClock:
    """A wall clock the test advances explicitly; never a sleep."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        return self._now

    def advance(self, *, milliseconds: int) -> None:
        self._now += timedelta(milliseconds=milliseconds)


class OperationWatcher:
    """Await published lifecycle transitions rather than polling the journal.

    The domain records a terminal on the worker thread *after* the worker
    resolves its own future, so a test that waited on anything else would race
    the transition it is asserting on.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._records: list[OperationRecord] = []

    def observe(self, record: OperationRecord) -> None:
        with self._condition:
            self._records.append(record)
            self._condition.notify_all()

    def wait_for(
        self,
        operation_id: str,
        state: OperationState,
        *,
        timeout: float = 2.0,
    ) -> OperationRecord:
        def matched() -> OperationRecord | None:
            for record in self._records:
                if record.operation_id == operation_id and record.state is state:
                    return record
            return None

        with self._condition:
            found = self._condition.wait_for(matched, timeout)
        if found is None:
            raise AssertionError(f"operation {operation_id} never reached {state}")
        return found

    def states(self, operation_id: str) -> list[OperationState]:
        with self._condition:
            return [record.state for record in self._records if record.operation_id == operation_id]

    @property
    def transitions(self) -> tuple[OperationRecord, ...]:
        with self._condition:
            return tuple(self._records)


class MachineWatcher:
    """Await published machine views; the domain latches faults on the worker thread."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._snapshots: list[MachineSnapshot] = []

    def observe(self, snapshot: MachineSnapshot) -> None:
        with self._condition:
            self._snapshots.append(snapshot)
            self._condition.notify_all()

    def wait_until(
        self,
        predicate: Callable[[MachineSnapshot], bool],
        *,
        timeout: float = 2.0,
    ) -> MachineSnapshot:
        def matched() -> MachineSnapshot | None:
            return next((view for view in reversed(self._snapshots) if predicate(view)), None)

        with self._condition:
            found = self._condition.wait_for(matched, timeout)
        if found is None:
            raise AssertionError("the machine view never reached the expected state")
        return found


@dataclass(slots=True)
class Harness:
    domain: OperationDomain
    simulator: SimulatorTransport
    journal: Journal
    watcher: OperationWatcher
    machine: MachineWatcher
    clock: FakeClock

    def submit(
        self,
        action: OperationAction = OperationAction.SORT,
        body: dict[str, Any] | None = None,
        *,
        key: str = "key-1",
        generation: int | None = None,
        deadline_ms: int = 5_000,
        actor: Actor = OPERATOR,
    ) -> OperationRecord:
        return self.domain.submit(
            action,
            SORT_BODY if body is None else body,
            actor=actor,
            idempotency_key=key,
            expected_generation=(
                self.domain.snapshot.generation if generation is None else generation
            ),
            deadline_ms=deadline_ms,
        )

    def stop(
        self,
        *,
        key: str = "stop-1",
        generation: int | None = None,
        deadline_ms: int = 5_000,
    ) -> OperationRecord:
        return self.domain.stop(
            actor=OPERATOR,
            idempotency_key=key,
            expected_generation=generation,
            deadline_ms=deadline_ms,
        )

    def break_journal(self) -> BreakableJournal:
        assert isinstance(self.journal, BreakableJournal)
        self.journal.broken = True
        return self.journal

    def home(self, *, key: str = "home") -> OperationRecord:
        """Complete a home operation; the firmware refuses to sort before one."""
        record = self.submit(OperationAction.HOME, HOME_BODY, key=key)
        assert self.simulator.wait_until_scheduled(timeout=1.0)
        self.simulator.advance(10_000)
        return self.watcher.wait_for(record.operation_id, OperationState.SUCCEEDED)

    def sort_commands(self) -> int:
        return sum(
            entry.direction is TranscriptDirection.HOST_TO_SIMULATOR
            and b" sortto:3\n" in entry.data
            for entry in self.simulator.transcript
        )


class BreakableJournal(Journal):
    """A journal whose writes can be broken the way a failing disk would.

    Reads keep working: a disk that cannot accept a write has usually not
    stopped answering, and the interesting question is what the daemon does
    when it cannot record what it is about to do.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.broken = False
        self._refused = 0
        self._refusals = threading.Condition()

    def record_admission(
        self,
        operation: OperationRecord,
        *,
        reason: str,
        idempotency: IdempotencyRecord | None = None,
    ) -> None:
        self._refuse_when_broken()
        super().record_admission(operation, reason=reason, idempotency=idempotency)

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
        self._refuse_when_broken()
        return super().record_transition(
            operation_id,
            to_state=to_state,
            generation=generation,
            occurred_at=occurred_at,
            reason=reason,
            trusted_terminal=trusted_terminal,
            outcome=outcome,
            protocol_request_id=protocol_request_id,
            terminal_fields=terminal_fields,
        )

    def wait_for_refusals(self, count: int, *, timeout: float = 2.0) -> None:
        """Await refused writes instead of polling for a fault to appear."""
        with self._refusals:
            if not self._refusals.wait_for(lambda: self._refused >= count, timeout):
                raise AssertionError(f"only {self._refused} of {count} writes were refused")

    def _refuse_when_broken(self) -> None:
        if not self.broken:
            return
        with self._refusals:
            self._refused += 1
            self._refusals.notify_all()
        raise JournalError("injected journal write failure")


class StopSwallowingTransport:
    """Drop the exact ID-less stop write, so no trusted terminal can arrive."""

    dtr_suppression_guaranteed = False

    def __init__(self, delegate: SimulatorTransport) -> None:
        self.delegate = delegate

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        return self.delegate.read(size, timeout=timeout)

    def write(self, data: bytes) -> int:
        if data == b"stop\n":
            return len(data)
        return self.delegate.write(data)

    def reset(self) -> None:
        self.delegate.reset()

    def close(self) -> None:
        self.delegate.close()


@pytest.fixture
def make_harness() -> Iterator[Callable[..., Harness]]:
    built: list[Harness] = []

    def make(
        *,
        scenario: AdverseScenario | None = None,
        normal_capacity: int = 4,
        start: bool = True,
        breakable: bool = False,
        swallow_stop: bool = False,
        slot_max: int = 102,
    ) -> Harness:
        clock = FakeClock(START)
        watcher = OperationWatcher()
        machine = MachineWatcher()
        config = (
            SimulatorConfig(slot_max=slot_max)
            if scenario is None
            else SimulatorConfig(scenario=scenario, slot_max=slot_max)
        )
        simulator = SimulatorTransport(config)
        opener = BreakableJournal.open if breakable else Journal.open
        journal = opener(IN_MEMORY, now=clock)
        transport = StopSwallowingTransport(simulator) if swallow_stop else simulator

        def worker_factory(observers: WorkerObservers) -> SerialWorker:
            return SerialWorker(
                lambda: transport,
                normal_capacity=normal_capacity,
                protocol_timeout=0.1,
                interrupt_poll_interval=0.005,
                session_observer=observers.session,
                profile_observer=observers.profile,
            )

        domain = OperationDomain(
            journal,
            worker_factory,
            now=clock,
            operation_observer=watcher.observe,
            machine_observer=machine.observe,
        )
        harness = Harness(domain, simulator, journal, watcher, machine, clock)
        built.append(harness)
        if start:
            domain.start(timeout=1.0)
        return harness

    yield make

    for harness in built:
        harness.domain.close(timeout=1.0)
        harness.journal.close()


def test_an_admitted_command_is_durable_with_identity_deadline_and_audit(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    harness.submit(OperationAction.HOME, HOME_BODY, key="blocker")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)

    # Admitted behind a blocker, so it is still queued when this is asserted.
    admitted = harness.submit(deadline_ms=5_000)

    assert UUID(admitted.operation_id).version == 4
    assert admitted.state is OperationState.ACCEPTED
    assert admitted.created_at == START
    assert admitted.deadline_at == START + timedelta(milliseconds=5_000)
    assert admitted.actor == OPERATOR
    transitions = harness.journal.transitions(admitted.operation_id)
    assert [entry.to_state for entry in transitions] == [
        OperationState.QUEUED,
        OperationState.ACCEPTED,
    ]
    assert [entry.reason for entry in transitions][0].startswith("admitted against generation")


def test_a_trusted_firmware_terminal_completes_the_operation(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    admitted = harness.submit()
    assert harness.simulator.wait_until_scheduled(timeout=1.0)

    harness.simulator.advance(10_000)
    final = harness.watcher.wait_for(admitted.operation_id, OperationState.SUCCEEDED)

    assert final.trusted_terminal
    assert final.outcome == "done"
    assert final.protocol_request_id is not None
    assert harness.watcher.states(admitted.operation_id) == [
        OperationState.ACCEPTED,
        OperationState.RUNNING,
        OperationState.SUCCEEDED,
    ]


def test_a_correlated_error_terminal_fails_rather_than_succeeds(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(scenario=AdverseScenario.FAULT)
    harness.home()
    admitted = harness.submit()
    assert harness.simulator.wait_until_scheduled(timeout=1.0)

    harness.simulator.advance(10_000)
    final = harness.watcher.wait_for(admitted.operation_id, OperationState.FAILED)

    assert not final.trusted_terminal
    assert final.outcome == "feed_overtravel"
    assert OperationState.SUCCEEDED not in harness.watcher.states(admitted.operation_id)


def test_every_material_transition_advances_the_generation(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    admitted = harness.submit()
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    harness.simulator.advance(10_000)
    harness.watcher.wait_for(admitted.operation_id, OperationState.SUCCEEDED)

    generations = [snapshot.generation for snapshot in harness.domain.machine.history]
    recorded = [entry.generation for entry in harness.journal.transitions(admitted.operation_id)]

    # Every material change moves the machine view by exactly one, and each
    # lifecycle transition durably records the generation it published.
    assert generations == list(range(1, len(generations) + 1))
    assert recorded == sorted(set(recorded))
    assert recorded[1] == admitted.generation
    assert recorded[-1] == harness.domain.snapshot.generation


def test_replaying_a_key_with_an_equivalent_request_returns_the_original(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    admitted = harness.submit(key="retry-1")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    harness.simulator.advance(10_000)
    harness.watcher.wait_for(admitted.operation_id, OperationState.SUCCEEDED)

    # Deliberately stale: a retry that lost the race for a fresh snapshot must
    # still deduplicate rather than start a second physical movement.
    replayed = harness.submit(key="retry-1", generation=admitted.generation)

    assert replayed.operation_id == admitted.operation_id
    assert replayed.state is OperationState.SUCCEEDED
    assert harness.sort_commands() == 1


def test_reusing_a_key_for_a_different_request_conflicts(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    original = harness.submit(key="retry-2", body={"slot": 3})
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    harness.simulator.advance(10_000)
    harness.watcher.wait_for(original.operation_id, OperationState.SUCCEEDED)

    with pytest.raises(IdempotencyConflictError) as raised:
        harness.submit(key="retry-2", body={"slot": 4})

    assert raised.value.code == "IDEMPOTENCY_CONFLICT"
    assert harness.sort_commands() == 1


def test_a_different_actor_reusing_a_key_conflicts(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    harness.submit(key="retry-3")

    with pytest.raises(IdempotencyConflictError):
        harness.submit(key="retry-3", actor=Actor(user_id="someone-else", role="operator"))


def test_a_stale_generation_is_rejected_before_any_serial_io(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    current = harness.domain.snapshot.generation

    with pytest.raises(StaleGenerationError) as raised:
        harness.submit(generation=current - 1)

    assert raised.value.code == "STALE_GENERATION"
    assert raised.value.operation_id is None
    # Nothing was journaled, nothing was published, nothing reached the wire.
    assert harness.watcher.transitions == ()
    assert harness.sort_commands() == 0
    assert harness.domain.snapshot.generation == current


def test_work_is_refused_while_the_machine_does_not_admit_it(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(start=False)

    with pytest.raises(NotReadyError) as raised:
        harness.submit(generation=1)

    assert raised.value.code == "NOT_READY"
    assert harness.sort_commands() == 0


@pytest.mark.parametrize("deadline_ms", [0, -1, 99, 600_001, True])
def test_a_deadline_outside_policy_is_refused_before_admission(
    make_harness: Callable[..., Harness],
    deadline_ms: int,
) -> None:
    harness = make_harness()

    with pytest.raises(DeadlineInvalidError) as raised:
        harness.submit(deadline_ms=deadline_ms)

    assert raised.value.code == "DEADLINE_INVALID"
    assert harness.sort_commands() == 0


def test_a_deadline_that_expires_before_dispatch_fails_without_transmission(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    blocker = harness.submit(OperationAction.HOME, HOME_BODY, key="blocker")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    expiring = harness.submit(key="expiring", deadline_ms=100)

    harness.clock.advance(milliseconds=500)
    harness.simulator.advance(10_000)
    harness.watcher.wait_for(blocker.operation_id, OperationState.SUCCEEDED)
    failed = harness.watcher.wait_for(expiring.operation_id, OperationState.FAILED)

    assert failed.outcome == "DEADLINE_EXPIRED"
    assert harness.watcher.states(expiring.operation_id) == [
        OperationState.ACCEPTED,
        OperationState.FAILED,
    ]
    assert harness.sort_commands() == 0


def test_a_saturated_lane_is_rejected_and_the_attempt_stays_durable(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(normal_capacity=1)
    harness.home()
    harness.submit(OperationAction.HOME, HOME_BODY, key="active")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    harness.submit(key="queued")

    with pytest.raises(OperationQueueFullError) as raised:
        harness.submit(key="rejected")

    assert raised.value.code == "QUEUE_FULL"
    rejected_id = raised.value.operation_id
    assert rejected_id is not None
    rejected = harness.domain.operation(rejected_id)
    assert rejected is not None
    assert rejected.state is OperationState.CANCELLED


def test_an_unverified_terminal_makes_the_operation_uncertain(
    make_harness: Callable[..., Harness],
) -> None:
    """Closes the PI-SIM-002 criterion deferred until an operation domain existed."""
    harness = make_harness(scenario=AdverseScenario.TERMINAL_MISMATCH)
    harness.home()
    admitted = harness.submit()

    final = harness.watcher.wait_for(admitted.operation_id, OperationState.UNCERTAIN)

    assert not final.trusted_terminal
    assert final.outcome == "RecoveryError"
    assert OperationState.SUCCEEDED not in harness.watcher.states(admitted.operation_id)
    assert harness.sort_commands() == 1


def test_session_recovery_cancels_queued_work_without_replaying_it(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(scenario=AdverseScenario.TERMINAL_MISMATCH)
    harness.home()
    blocker = harness.submit(OperationAction.HOME, HOME_BODY, key="blocker")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    breaking = harness.submit(key="breaking")
    queued = harness.submit(OperationAction.HOME, HOME_BODY, key="queued")

    harness.simulator.advance(10_000)
    harness.watcher.wait_for(blocker.operation_id, OperationState.SUCCEEDED)
    harness.watcher.wait_for(breaking.operation_id, OperationState.UNCERTAIN)
    cancelled = harness.watcher.wait_for(queued.operation_id, OperationState.CANCELLED)

    assert cancelled.outcome == "PreemptedByRecoveryError"
    assert harness.sort_commands() == 1


def test_only_one_command_can_be_admitted_against_one_observed_generation(
    make_harness: Callable[..., Harness],
) -> None:
    """Concurrent admission is serialized, and admitting moves the view.

    This also exercises the lock order. Admission holds the machine lock while
    it journals and releases it before enqueueing, and the worker thread enters
    that lock holding nothing, so four racing callers cannot deadlock against a
    dispatching worker.
    """
    harness = make_harness(normal_capacity=16)
    observed = harness.domain.snapshot.generation
    admitted: list[OperationRecord] = []
    failures: list[BaseException] = []
    barrier = threading.Barrier(4)

    def admit(index: int) -> None:
        try:
            barrier.wait(2.0)
            admitted.append(
                harness.submit(
                    OperationAction.HOME,
                    HOME_BODY,
                    key=f"concurrent-{index}",
                    generation=observed,
                )
            )
        except BaseException as exc:  # noqa: BLE001 - reported to the main thread
            failures.append(exc)

    threads = [threading.Thread(target=admit, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5.0)

    assert not any(thread.is_alive() for thread in threads)
    assert len(admitted) == 1
    assert [type(failure) for failure in failures] == [StaleGenerationError] * 3
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    harness.simulator.advance(10_000)
    harness.watcher.wait_for(admitted[0].operation_id, OperationState.SUCCEEDED, timeout=5.0)


def test_a_priority_stop_creates_an_attributable_operation_and_clears_queued_work(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    active = harness.submit(OperationAction.HOME, HOME_BODY, key="active")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    queued = harness.submit(OperationAction.HOME, HOME_BODY, key="queued")

    stop = harness.stop()

    assert stop.action is OperationAction.STOP
    assert stop.actor == OPERATOR
    assert stop.state is OperationState.RUNNING
    settled = harness.watcher.wait_for(stop.operation_id, OperationState.SUCCEEDED)
    assert settled.trusted_terminal
    assert settled.outcome == "stopped"
    cancelled = harness.watcher.wait_for(queued.operation_id, OperationState.CANCELLED)
    assert cancelled.outcome == "PreemptedByStopError"
    harness.watcher.wait_for(active.operation_id, OperationState.CANCELLED)


def test_a_stop_without_a_trusted_terminal_leaves_affected_work_uncertain(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(swallow_stop=True)
    active = harness.submit(OperationAction.HOME, HOME_BODY, key="active")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)

    stop = harness.stop()

    stopped = harness.watcher.wait_for(stop.operation_id, OperationState.UNCERTAIN, timeout=5.0)
    affected = harness.watcher.wait_for(active.operation_id, OperationState.UNCERTAIN, timeout=5.0)
    assert not stopped.trusted_terminal
    assert not affected.trusted_terminal
    assert OperationState.SUCCEEDED not in harness.watcher.states(stop.operation_id)
    assert OperationState.SUCCEEDED not in harness.watcher.states(active.operation_id)


def test_a_replayed_stop_key_returns_the_original_stop(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    first = harness.stop(key="stop-retry")

    replayed = harness.stop(key="stop-retry")

    assert replayed.operation_id == first.operation_id


def test_a_stop_is_recorded_even_when_the_worker_cannot_carry_it_out(
    make_harness: Callable[..., Harness],
) -> None:
    """Stop skips the readiness check that ordinary motion must pass."""
    harness = make_harness(start=False)

    with pytest.raises(NotReadyError) as raised:
        harness.stop()

    operation_id = raised.value.operation_id
    assert operation_id is not None
    recorded = harness.domain.operation(operation_id)
    assert recorded is not None
    assert recorded.action is OperationAction.STOP
    assert recorded.state is OperationState.CANCELLED


def test_a_journal_failure_blocks_new_motion_and_latches_the_machine(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(breakable=True)
    harness.home()
    harness.break_journal()

    with pytest.raises(JournalUnavailableError) as raised:
        harness.submit(key="blocked")

    assert raised.value.code == "JOURNAL_UNAVAILABLE"
    view = harness.domain.snapshot
    assert not view.journal_available
    assert view.fault is FaultState.LATCHED
    assert not view.admits_work
    assert harness.sort_commands() == 0

    # The latched machine keeps refusing without touching the journal again.
    with pytest.raises(JournalUnavailableError):
        harness.submit(key="blocked-again")
    assert harness.sort_commands() == 0


def test_a_journal_failure_cannot_yield_an_unrecorded_success(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(breakable=True)
    harness.home()
    admitted = harness.submit(key="in-flight")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    journal = harness.break_journal()

    harness.simulator.advance(10_000)
    journal.wait_for_refusals(1)
    # The refusal and the latch are two steps on the worker thread; wait for
    # the published view rather than assuming the second already happened.
    harness.machine.wait_until(lambda view: not view.journal_available)

    recorded = harness.domain.operation(admitted.operation_id)
    assert recorded is not None
    assert recorded.state is OperationState.RUNNING
    assert not recorded.trusted_terminal
    assert OperationState.SUCCEEDED not in harness.watcher.states(admitted.operation_id)
    assert not harness.domain.snapshot.admits_work


def test_a_lifecycle_write_that_fails_stops_the_command_before_transmission(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(breakable=True)
    harness.home()
    harness.submit(OperationAction.HOME, HOME_BODY, key="blocker")
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    queued = harness.submit(key="queued")
    journal = harness.break_journal()

    harness.simulator.advance(10_000)
    # The blocker's terminal is refused first, then the queued command's
    # dispatch gate; after that the command can no longer be transmitted.
    journal.wait_for_refusals(2)
    harness.machine.wait_until(lambda view: not view.journal_available)

    assert harness.sort_commands() == 0
    recorded = harness.domain.operation(queued.operation_id)
    assert recorded is not None
    assert recorded.state is OperationState.ACCEPTED
    assert not harness.domain.snapshot.journal_available


def test_a_stop_that_cannot_be_recorded_is_refused(
    make_harness: Callable[..., Harness],
) -> None:
    """A software stop the daemon cannot attribute is not a stop it may claim."""
    harness = make_harness(breakable=True)
    harness.break_journal()

    with pytest.raises(JournalUnavailableError) as raised:
        harness.stop()

    assert raised.value.code == "JOURNAL_UNAVAILABLE"
    assert not harness.domain.snapshot.journal_available


def test_the_machine_view_publishes_observed_capabilities_and_readiness(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()

    firmware = harness.domain.snapshot.firmware
    readiness = harness.domain.snapshot.readiness

    assert firmware is not None
    assert firmware.protocol_version == 2
    assert firmware.slot_max == 102
    assert firmware.sort_home and firmware.feed_home
    assert readiness is not None
    # Nothing is assumed: a fresh controller has not homed anything.
    assert not readiness.sort_homed

    harness.home()

    refreshed = harness.domain.snapshot.readiness
    assert refreshed is not None
    assert refreshed.sort_homed


def test_a_sort_before_homing_is_refused_before_any_serial_io(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()

    with pytest.raises(NotReadyError, match="sorter position is unknown") as raised:
        harness.submit()

    assert raised.value.code == "NOT_READY"
    assert harness.sort_commands() == 0
    assert harness.watcher.transitions == ()


def test_a_slot_beyond_the_advertised_range_is_refused_before_any_serial_io(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(slot_max=8)
    harness.home()

    with pytest.raises(ValidationError, match="advertised maximum 8") as raised:
        harness.submit(body={"slot": 9})

    assert raised.value.code == "VALIDATION_FAILED"
    assert harness.domain.operation(raised.value.operation_id or "") is None


def test_feed_is_refused_without_serial_io_while_its_firmware_gate_is_open(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    harness.home()
    before = harness.domain.snapshot.generation

    with pytest.raises(UnsupportedOperationError, match=FEED_LIFECYCLE_GATE) as raised:
        harness.submit(OperationAction.FEED, {"slot": 3}, key="feed-1")

    assert raised.value.code == "UNSUPPORTED"
    # Refused before admission: no operation, no generation change, no bytes.
    assert harness.domain.snapshot.generation == before
    assert not any(record.action is OperationAction.FEED for record in harness.watcher.transitions)


def test_a_trusted_terminal_records_the_fields_the_controller_reported(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness()
    homed = harness.home()
    admitted = harness.submit()
    assert harness.simulator.wait_until_scheduled(timeout=1.0)
    # While the movement runs, the machine view names the active operation.
    assert harness.domain.snapshot.active_operation_id == admitted.operation_id

    harness.simulator.advance(10_000)
    sorted_record = harness.watcher.wait_for(admitted.operation_id, OperationState.SUCCEEDED)

    assert sorted_record.terminal_fields == {"slot": "3"}
    assert homed.terminal_fields is not None
    assert harness.domain.snapshot.active_operation_id is None

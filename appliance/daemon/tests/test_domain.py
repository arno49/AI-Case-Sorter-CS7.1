from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from cs71d.domain import (
    DeadlineInvalidError,
    IdempotencyConflictError,
    NotReadyError,
    OperationDomain,
    OperationQueueFullError,
    StaleGenerationError,
)
from cs71d.journal import IN_MEMORY, Journal
from cs71d.operations import Actor, OperationAction, OperationRecord, OperationState
from cs71d.serial_worker import SerialWorker
from cs71d.session import SessionSnapshot
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


@dataclass(slots=True)
class Harness:
    domain: OperationDomain
    simulator: SimulatorTransport
    journal: Journal
    watcher: OperationWatcher
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


@pytest.fixture
def make_harness() -> Iterator[Callable[..., Harness]]:
    built: list[Harness] = []

    def make(
        *,
        scenario: AdverseScenario | None = None,
        normal_capacity: int = 4,
        start: bool = True,
    ) -> Harness:
        clock = FakeClock(START)
        watcher = OperationWatcher()
        config = SimulatorConfig() if scenario is None else SimulatorConfig(scenario=scenario)
        simulator = SimulatorTransport(config)
        journal = Journal.open(IN_MEMORY, now=clock)

        def worker_factory(observer: Callable[[SessionSnapshot], None]) -> SerialWorker:
            return SerialWorker(
                lambda: simulator,
                normal_capacity=normal_capacity,
                protocol_timeout=0.1,
                interrupt_poll_interval=0.005,
                session_observer=observer,
            )

        domain = OperationDomain(
            journal,
            worker_factory,
            now=clock,
            operation_observer=watcher.observe,
        )
        harness = Harness(domain, simulator, journal, watcher, clock)
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

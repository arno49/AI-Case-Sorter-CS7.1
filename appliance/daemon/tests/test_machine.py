from __future__ import annotations

import threading

import pytest

from cs71d.machine import MachineSnapshot, MachineState
from cs71d.session import ConnectionState, SessionSnapshot


def _session(state: ConnectionState, generation: int = 1) -> SessionSnapshot:
    return SessionSnapshot(state, generation, f"entering {state}")


def test_the_machine_view_starts_disconnected_at_generation_one() -> None:
    machine = MachineState()

    assert machine.snapshot == MachineSnapshot(1, ConnectionState.DISCONNECTED, "initial", None)
    assert not machine.snapshot.admits_work


def test_connection_transitions_advance_the_machine_generation() -> None:
    machine = MachineState()
    walk = [
        ConnectionState.CONNECTING,
        ConnectionState.VERIFYING_V1,
        ConnectionState.ACTIVATING_V2,
        ConnectionState.READY,
    ]

    for state in walk:
        machine.observe_connection(_session(state))

    assert [snapshot.connection for snapshot in machine.history] == [
        ConnectionState.DISCONNECTED,
        *walk,
    ]
    assert [snapshot.generation for snapshot in machine.history] == [1, 2, 3, 4, 5]


def test_reobserving_the_same_connection_is_not_material() -> None:
    machine = MachineState()
    machine.observe_connection(_session(ConnectionState.RECOVERING))

    unchanged = machine.observe_connection(_session(ConnectionState.RECOVERING, generation=9))

    assert unchanged.generation == 2
    assert len(machine.history) == 2


def test_only_a_ready_connection_admits_work() -> None:
    machine = MachineState()
    admitting = []
    for state in ConnectionState:
        machine.observe_connection(_session(state))
        if machine.snapshot.admits_work:
            admitting.append(state)

    assert admitting == [ConnectionState.READY]


def test_a_transition_publishes_exactly_the_generation_it_yielded() -> None:
    machine = MachineState()

    with machine.transition("operation-1", "running") as generation:
        assert generation == 2
        # Nothing is published until the durable write inside the block returns.
        assert machine.generation == 1

    assert machine.snapshot.generation == 2
    assert machine.snapshot.active_operation_id == "operation-1"
    assert machine.snapshot.reason == "running"


def test_a_failed_durable_write_publishes_nothing() -> None:
    machine = MachineState()
    machine.observe_connection(_session(ConnectionState.READY))
    before = machine.snapshot

    with (
        pytest.raises(RuntimeError, match="journal"),
        machine.transition("operation-1", "running"),
    ):
        raise RuntimeError("journal write failed")

    assert machine.snapshot == before
    assert len(machine.history) == 2


def test_settling_an_operation_clears_the_active_identity() -> None:
    machine = MachineState()
    with machine.transition("operation-1", "running"):
        pass

    with machine.transition(None, "succeeded"):
        pass

    assert machine.snapshot.active_operation_id is None
    assert machine.snapshot.generation == 3


def test_an_admission_decision_excludes_a_concurrent_publisher() -> None:
    machine = MachineState()
    entered = threading.Event()
    published = threading.Event()

    def publisher() -> None:
        entered.wait(2.0)
        machine.observe_connection(_session(ConnectionState.READY))
        published.set()

    thread = threading.Thread(target=publisher, name="publisher")
    thread.start()
    try:
        with machine.admission() as view:
            entered.set()
            # A bounded negative wait: the publisher is blocked on the machine
            # lock, so the decision keeps observing the generation it validated
            # against. This checks mutual exclusion, it is not synchronization.
            assert not published.wait(0.2)
            assert machine.snapshot == view
    finally:
        thread.join(2.0)

    assert published.is_set()
    assert machine.snapshot.connection is ConnectionState.READY

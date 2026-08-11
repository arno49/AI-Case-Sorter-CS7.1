from __future__ import annotations

from cs71d import ConnectionState, SessionSnapshot, SessionState


def test_initial_snapshot_is_disconnected_at_generation_one() -> None:
    session = SessionState()

    assert session.snapshot == SessionSnapshot(ConnectionState.DISCONNECTED, 1, "initial")
    assert not session.snapshot.admits_work


def test_each_material_transition_increments_generation_monotonically() -> None:
    session = SessionState()
    walk = [
        ConnectionState.CONNECTING,
        ConnectionState.VERIFYING_V1,
        ConnectionState.ACTIVATING_V2,
        ConnectionState.READY,
    ]

    for state in walk:
        session.transition(state, f"entering {state}")

    assert [snapshot.state for snapshot in session.history] == [
        ConnectionState.DISCONNECTED,
        *walk,
    ]
    assert [snapshot.generation for snapshot in session.history] == [1, 2, 3, 4, 5]


def test_reentering_the_same_state_is_not_material() -> None:
    session = SessionState()
    session.transition(ConnectionState.RECOVERING, "first reason")

    unchanged = session.transition(ConnectionState.RECOVERING, "second reason")

    assert unchanged.generation == 2
    assert unchanged.reason == "first reason"
    assert len(session.history) == 2


def test_only_ready_admits_work() -> None:
    session = SessionState()
    admitting = []
    for state in ConnectionState:
        session.transition(state, "walk")
        if session.snapshot.admits_work:
            admitting.append(state)

    assert admitting == [ConnectionState.READY]


def test_observer_sees_every_published_snapshot() -> None:
    seen: list[SessionSnapshot] = []
    session = SessionState(observer=seen.append)

    session.transition(ConnectionState.CONNECTING, "opening")
    session.transition(ConnectionState.CONNECTING, "ignored duplicate")
    session.transition(ConnectionState.UNCERTAIN, "broken")

    assert [snapshot.state for snapshot in seen] == [
        ConnectionState.CONNECTING,
        ConnectionState.UNCERTAIN,
    ]
    assert [snapshot.generation for snapshot in seen] == [2, 3]

from __future__ import annotations

import ast
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest
from cs71_protocol import Completion, RecoveryError, Status

from cs71d import (
    ConnectionState,
    HomeAxis,
    HomeIntent,
    PreemptedByRecoveryError,
    PreemptedByStopError,
    QueryIntent,
    QueryKind,
    QueueFullError,
    SerialWorker,
    SessionNotReadyError,
    SessionProfile,
    SessionSnapshot,
    SortIntent,
    WorkerStartupError,
    WorkerState,
)
from cs71d.serial_worker import WorkerResult
from cs71d.simulator import (
    AdverseScenario,
    SimulatorConfig,
    SimulatorTransport,
    TranscriptDirection,
)


def _completion(future: Future[WorkerResult], *, timeout: float = 0.5) -> Completion:
    result = future.result(timeout=timeout)
    assert isinstance(result, Completion)
    return result


class ThreadRecordingTransport:
    dtr_suppression_guaranteed = False

    def __init__(self, delegate: SimulatorTransport) -> None:
        self.delegate = delegate
        self.calls: list[tuple[str, int]] = []

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        self._record("read")
        return self.delegate.read(size, timeout=timeout)

    def write(self, data: bytes) -> int:
        self._record("write")
        return self.delegate.write(data)

    def reset(self) -> None:
        self._record("reset")
        self.delegate.reset()

    def close(self) -> None:
        self._record("close")
        self.delegate.close()

    def _record(self, operation: str) -> None:
        self.calls.append((operation, threading.get_ident()))


def _started_worker(
    *,
    normal_capacity: int = 4,
    config: SimulatorConfig | None = None,
) -> tuple[SerialWorker, SimulatorTransport, ThreadRecordingTransport, list[int]]:
    simulator = SimulatorTransport(config)
    transport = ThreadRecordingTransport(simulator)
    factory_threads: list[int] = []

    def factory() -> ThreadRecordingTransport:
        factory_threads.append(threading.get_ident())
        return transport

    worker = SerialWorker(
        factory,
        normal_capacity=normal_capacity,
        protocol_timeout=0.1,
        interrupt_poll_interval=0.005,
    )
    worker.start(timeout=0.5)
    return worker, simulator, transport, factory_threads


def test_worker_bootstraps_v2_and_all_io_stays_on_owner_thread() -> None:
    worker, _simulator, transport, factory_threads = _started_worker()

    status = worker.submit(QueryIntent(QueryKind.STATUS)).result(timeout=0.5)

    assert isinstance(status, Status)
    assert status.mode == "recovering"
    worker.close(timeout=0.5)
    assert worker.state is WorkerState.CLOSED
    assert worker.worker_thread_id is not None
    assert factory_threads == [worker.worker_thread_id]
    assert transport.calls
    assert {thread_id for _, thread_id in transport.calls} == {worker.worker_thread_id}


def test_priority_stop_preempts_active_work_and_clears_full_normal_lane() -> None:
    worker, simulator, _transport, _factory_threads = _started_worker(normal_capacity=1)
    active = worker.submit(HomeIntent(HomeAxis.BOTH))
    assert simulator.wait_until_scheduled(timeout=0.5)
    started_at_ms = simulator.clock.now_ms
    queued = worker.submit(QueryIntent(QueryKind.STATUS))

    with pytest.raises(QueueFullError):
        worker.submit(QueryIntent(QueryKind.QUEUE))

    first_stop = worker.submit_priority_stop()
    second_stop = worker.submit_priority_stop()

    first_stop.result(timeout=0.5)
    second_stop.result(timeout=0.5)
    with pytest.raises(PreemptedByStopError):
        active.result(timeout=0.5)
    with pytest.raises(PreemptedByStopError):
        queued.result(timeout=0.5)
    assert simulator.clock.now_ms == started_at_ms
    assert [
        entry.data
        for entry in simulator.transcript
        if entry.direction is TranscriptDirection.HOST_TO_SIMULATOR and entry.data == b"stop\n"
    ] == [b"stop\n"]
    worker.close(timeout=0.5)


def test_state_changing_intents_are_dispatched_one_at_a_time() -> None:
    worker, simulator, _transport, _factory_threads = _started_worker()
    home = worker.submit(HomeIntent(HomeAxis.BOTH))
    sort = worker.submit(SortIntent(3))
    assert simulator.wait_until_scheduled(timeout=0.5)

    host_lines = [
        entry.data
        for entry in simulator.transcript
        if entry.direction is TranscriptDirection.HOST_TO_SIMULATOR
    ]
    assert any(b" homeall\n" in line for line in host_lines)
    assert not any(b" sortto:3\n" in line for line in host_lines)

    simulator.advance(10_000)
    assert _completion(home).succeeded
    assert simulator.wait_until_scheduled(timeout=0.5)
    simulator.advance(10_000)
    assert _completion(sort).succeeded
    worker.close(timeout=0.5)


def test_cancelled_queued_future_is_never_dispatched() -> None:
    worker, simulator, _transport, _factory_threads = _started_worker()
    active = worker.submit(HomeIntent(HomeAxis.BOTH))
    assert simulator.wait_until_scheduled(timeout=0.5)
    queued = worker.submit(QueryIntent(QueryKind.QUEUE))
    assert queued.cancel()

    simulator.advance(10_000)

    assert _completion(active).succeeded
    assert queued.cancelled()
    assert not any(
        entry.direction is TranscriptDirection.HOST_TO_SIMULATOR and b" queue\n" in entry.data
        for entry in simulator.transcript
    )
    worker.close(timeout=0.5)


def test_concurrent_start_and_api_submission_open_only_one_transport() -> None:
    simulator = SimulatorTransport()
    transport = ThreadRecordingTransport(simulator)
    factory_calls = 0
    factory_lock = threading.Lock()

    def factory() -> ThreadRecordingTransport:
        nonlocal factory_calls
        with factory_lock:
            factory_calls += 1
        return transport

    worker = SerialWorker(factory, normal_capacity=32, protocol_timeout=0.1)
    with ThreadPoolExecutor(max_workers=8) as executor:
        starts = [executor.submit(worker.start, timeout=0.5) for _ in range(8)]
        for started in starts:
            started.result(timeout=1.0)
        submissions = list(
            executor.map(
                worker.submit,
                [QueryIntent(QueryKind.STATUS) for _ in range(16)],
            )
        )

    assert all(isinstance(future.result(timeout=1.0), Status) for future in submissions)
    assert factory_calls == 1
    worker.close(timeout=0.5)


def test_close_preempts_active_motion_without_advancing_simulator_time() -> None:
    worker, simulator, transport, _factory_threads = _started_worker()
    active = worker.submit(HomeIntent(HomeAxis.BOTH))
    assert simulator.wait_until_scheduled(timeout=0.5)
    started_at_ms = simulator.clock.now_ms

    worker.close(timeout=0.5)

    with pytest.raises(PreemptedByStopError):
        active.result(timeout=0.5)
    assert simulator.clock.now_ms == started_at_ms
    assert worker.state is WorkerState.CLOSED
    assert transport.calls[-1][0] == "close"


def test_v1_only_controller_fails_startup_and_closes_transport() -> None:
    simulator = SimulatorTransport(
        SimulatorConfig(scenario="legacy-v1", v2_available=False, crc_available=False)
    )
    transport = ThreadRecordingTransport(simulator)
    worker = SerialWorker(lambda: transport, protocol_timeout=0.02)

    with pytest.raises(WorkerStartupError):
        worker.start(timeout=0.5)

    assert worker.state is WorkerState.FAILED
    assert simulator.closed


class SessionWatcher:
    """Observe published snapshots so tests can await a transition, not a clock.

    The worker fails an operation's future before it finishes recovering, so a
    test that only awaits the future would race the recovery it is asserting on.
    """

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.snapshots: list[SessionSnapshot] = []

    def observe(self, snapshot: SessionSnapshot) -> None:
        with self._condition:
            self.snapshots.append(snapshot)
            self._condition.notify_all()

    def wait_for(self, state: ConnectionState, *, count: int = 1, timeout: float = 2.0) -> None:
        with self._condition:
            reached = self._condition.wait_for(
                lambda: sum(1 for item in self.snapshots if item.state is state) >= count,
                timeout,
            )
        assert reached, f"session never reached {state} {count}x: {self.snapshots}"


class UnresettableTransport:
    """A transport with no reset hook, so `cs71_protocol` cannot verify v1."""

    dtr_suppression_guaranteed = False

    def __init__(self, delegate: SimulatorTransport) -> None:
        self.delegate = delegate

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        return self.delegate.read(size, timeout=timeout)

    def write(self, data: bytes) -> int:
        return self.delegate.write(data)

    def close(self) -> None:
        self.delegate.close()


def _sort_command_count(simulator: SimulatorTransport) -> int:
    return sum(
        entry.direction is TranscriptDirection.HOST_TO_SIMULATOR and b" sortto:3\n" in entry.data
        for entry in simulator.transcript
    )


CONNECTION_WALK = [
    ConnectionState.DISCONNECTED,
    ConnectionState.CONNECTING,
    ConnectionState.VERIFYING_V1,
    ConnectionState.ACTIVATING_V2,
    ConnectionState.READY,
]


def test_bootstrap_publishes_the_connection_walk_with_monotonic_generations() -> None:
    worker, _simulator, _transport, _factory_threads = _started_worker()

    history = worker.session_history
    assert [snapshot.state for snapshot in history] == CONNECTION_WALK
    assert [snapshot.generation for snapshot in history] == [1, 2, 3, 4, 5]
    assert worker.session.admits_work
    worker.close(timeout=0.5)


def test_unsafe_exchange_recovers_to_ready_and_never_replays_the_command() -> None:
    watcher = SessionWatcher()
    simulator = SimulatorTransport(SimulatorConfig(scenario=AdverseScenario.MALFORMED_FRAME.value))
    worker = SerialWorker(
        lambda: simulator,
        protocol_timeout=0.1,
        interrupt_poll_interval=0.005,
        session_observer=watcher.observe,
    )
    worker.start(timeout=0.5)
    active = worker.submit(SortIntent(3))
    queued = worker.submit(QueryIntent(QueryKind.STATUS))

    with pytest.raises(RecoveryError) as unsafe:
        active.result(timeout=1.0)
    with pytest.raises(PreemptedByRecoveryError):
        queued.result(timeout=1.0)
    watcher.wait_for(ConnectionState.READY, count=2)

    # The library recovered to a verified v1 session, so the worker re-activates.
    assert unsafe.value.recovered
    assert [snapshot.state for snapshot in worker.session_history] == [
        *CONNECTION_WALK,
        ConnectionState.RECOVERING,
        ConnectionState.VERIFYING_V1,
        ConnectionState.ACTIVATING_V2,
        ConnectionState.READY,
    ]
    assert worker.session.generation == 9
    assert _sort_command_count(simulator) == 1
    worker.close(timeout=0.5)


def test_submission_is_refused_while_the_session_is_recovering() -> None:
    watcher = SessionWatcher()
    refusals: list[Exception] = []

    def observer(snapshot: SessionSnapshot) -> None:
        watcher.observe(snapshot)
        if snapshot.state is not ConnectionState.RECOVERING:
            return
        try:
            worker.submit(QueryIntent(QueryKind.STATUS))
        except Exception as exc:  # noqa: BLE001 - the test records whatever is raised
            refusals.append(exc)

    simulator = SimulatorTransport(SimulatorConfig(scenario=AdverseScenario.TIMEOUT.value))
    worker = SerialWorker(
        lambda: simulator,
        protocol_timeout=0.1,
        interrupt_poll_interval=0.005,
        session_observer=observer,
    )
    worker.start(timeout=0.5)

    with pytest.raises(RecoveryError):
        worker.submit(SortIntent(3)).result(timeout=1.0)
    watcher.wait_for(ConnectionState.READY, count=2)

    # The worker thread stays healthy; only session confidence gates admission.
    assert [type(error) for error in refusals] == [SessionNotReadyError]
    assert worker.state is WorkerState.RUNNING
    worker.close(timeout=0.5)


def test_unverified_recovery_reconnects_from_disconnected_without_replay() -> None:
    simulators: list[SimulatorTransport] = []

    def factory() -> UnresettableTransport | SimulatorTransport:
        # The first controller cannot be reset, so in-session recovery fails and
        # the worker must escalate to a full reconnect on a fresh transport.
        first = not simulators
        simulator = SimulatorTransport(
            SimulatorConfig(scenario=AdverseScenario.TIMEOUT.value if first else "happy-v2")
        )
        simulators.append(simulator)
        return UnresettableTransport(simulator) if first else simulator

    watcher = SessionWatcher()
    worker = SerialWorker(
        factory,
        protocol_timeout=0.1,
        interrupt_poll_interval=0.005,
        session_observer=watcher.observe,
    )
    worker.start(timeout=0.5)

    with pytest.raises(RecoveryError) as unsafe:
        worker.submit(SortIntent(3)).result(timeout=1.0)
    watcher.wait_for(ConnectionState.READY, count=2)

    assert not unsafe.value.recovered
    assert [snapshot.state for snapshot in worker.session_history] == [
        *CONNECTION_WALK,
        ConnectionState.RECOVERING,
        ConnectionState.DISCONNECTED,
        ConnectionState.CONNECTING,
        ConnectionState.VERIFYING_V1,
        ConnectionState.ACTIVATING_V2,
        ConnectionState.READY,
    ]
    assert len(simulators) == 2
    assert simulators[0].closed
    assert _sort_command_count(simulators[1]) == 0
    worker.close(timeout=0.5)


def test_unrecoverable_session_becomes_uncertain_rather_than_ready() -> None:
    watcher = SessionWatcher()
    simulator = SimulatorTransport(SimulatorConfig(scenario=AdverseScenario.TIMEOUT.value))
    worker = SerialWorker(
        lambda: UnresettableTransport(simulator),
        protocol_timeout=0.1,
        interrupt_poll_interval=0.005,
        max_reconnect_attempts=0,
        session_observer=watcher.observe,
    )
    worker.start(timeout=0.5)

    with pytest.raises(RecoveryError):
        worker.submit(SortIntent(3)).result(timeout=1.0)
    watcher.wait_for(ConnectionState.UNCERTAIN)

    assert worker.session.state is ConnectionState.UNCERTAIN
    assert not worker.session.admits_work
    assert worker.state is WorkerState.FAILED
    assert simulator.closed
    worker.close(timeout=0.5)


def _modules_importing(symbol: str) -> set[str]:
    package_root = Path(__file__).parents[1] / "src/cs71d"
    importers: set[str] = set()
    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == symbol for alias in node.names
            ):
                importers.add(str(path.relative_to(package_root)))
    return importers


def test_protocol_client_import_is_confined_to_serial_worker() -> None:
    assert _modules_importing("ProtocolClient") == {"serial_worker.py"}


def test_real_serial_transport_import_is_confined_to_device_policy() -> None:
    assert _modules_importing("SerialTransport") == {"device.py"}


def test_importing_the_package_never_pulls_in_a_real_serial_backend() -> None:
    """`SerialTransport` is imported lazily so `import cs71d` needs no pyserial."""
    device_source = Path(__file__).parents[1] / "src/cs71d/device.py"
    tree = ast.parse(device_source.read_text(encoding="utf-8"), filename=str(device_source))
    module_scope_imports = {
        alias.name for node in tree.body if isinstance(node, ast.ImportFrom) for alias in node.names
    }

    assert "SerialTransport" not in module_scope_imports


def test_a_dispatch_hook_runs_on_the_owner_thread_before_transmission() -> None:
    worker, simulator, _transport, _factory_threads = _started_worker()
    hook_threads: list[int] = []
    commands_at_hook: list[int] = []

    def hook() -> None:
        hook_threads.append(threading.get_ident())
        commands_at_hook.append(_sort_command_count(simulator))

    completion = _completion(worker.submit(SortIntent(3), on_dispatch=hook))

    assert hook_threads == [worker.worker_thread_id]
    # The hook is the last moment before the first byte is written.
    assert commands_at_hook == [0]
    assert _sort_command_count(simulator) == 1
    assert not completion.succeeded
    worker.close(timeout=0.5)


def test_a_refusing_dispatch_hook_withdraws_the_intent_without_touching_the_session() -> None:
    worker, simulator, _transport, _factory_threads = _started_worker()

    class Withdrawn(RuntimeError):
        """Raised by a caller whose own preconditions expired while queued."""

    def refuse() -> None:
        raise Withdrawn("caller withdrew the intent")

    future = worker.submit(SortIntent(3), on_dispatch=refuse)

    with pytest.raises(Withdrawn):
        future.result(timeout=0.5)
    assert _sort_command_count(simulator) == 0
    assert worker.session.state is ConnectionState.READY
    assert isinstance(worker.submit(QueryIntent(QueryKind.STATUS)).result(timeout=0.5), Status)
    worker.close(timeout=0.5)


def test_required_snapshots_are_gathered_before_ready_and_after_each_movement() -> None:
    profiles: list[SessionProfile] = []
    states: list[ConnectionState] = []
    simulator = SimulatorTransport(SimulatorConfig())

    def on_session(snapshot: SessionSnapshot) -> None:
        states.append(snapshot.state)
        if snapshot.state is ConnectionState.READY:
            # READY may only be published once the snapshots already exist.
            assert profiles

    worker = SerialWorker(
        lambda: simulator,
        protocol_timeout=0.1,
        session_observer=on_session,
        profile_observer=profiles.append,
    )
    worker.start(timeout=0.5)
    assert len(profiles) == 1
    assert profiles[0].capabilities.slot_max == 102
    assert not profiles[0].status.sort_homed

    home = worker.submit(HomeIntent(HomeAxis.BOTH))
    assert simulator.wait_until_scheduled(timeout=0.5)
    simulator.advance(10_000)
    assert _completion(home).succeeded

    assert len(profiles) == 2
    assert profiles[1].status.sort_homed
    assert ConnectionState.READY in states
    worker.close(timeout=0.5)

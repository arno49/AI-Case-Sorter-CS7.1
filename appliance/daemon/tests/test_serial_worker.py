from __future__ import annotations

import ast
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest
from cs71_protocol import Completion, Status

from cs71d import (
    HomeAxis,
    HomeIntent,
    PreemptedByStopError,
    QueryIntent,
    QueryKind,
    QueueFullError,
    SerialWorker,
    SortIntent,
    WorkerStartupError,
    WorkerState,
)
from cs71d.serial_worker import WorkerResult
from cs71d.simulator import SimulatorConfig, SimulatorTransport, TranscriptDirection


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


def test_protocol_client_import_is_confined_to_serial_worker() -> None:
    package_root = Path(__file__).parents[1] / "src/cs71d"
    offenders: list[str] = []
    for path in package_root.rglob("*.py"):
        if path.name == "serial_worker.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(
                alias.name == "ProtocolClient" for alias in node.names
            ):
                offenders.append(str(path.relative_to(package_root)))

    assert offenders == []

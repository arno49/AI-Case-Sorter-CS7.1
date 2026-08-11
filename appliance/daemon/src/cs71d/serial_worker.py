"""Single-owner protocol worker with bounded admission and priority stop."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Protocol, TypeVar

from cs71_protocol import (
    ByteStream,
    Capabilities,
    Completion,
    ProtocolClient,
    QueueSnapshot,
    RequestInterruptedError,
    Status,
)


class OwnedByteStream(ByteStream, Protocol):
    """Byte stream whose lifecycle is owned by the serial worker."""

    def close(self) -> None:
        """Release the transport."""


class WorkerState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class QueryKind(StrEnum):
    STATUS = "status"
    CAPABILITIES = "capabilities"
    QUEUE = "queue"


class HomeAxis(StrEnum):
    FEEDER = "feeder"
    SORTER = "sorter"
    BOTH = "both"


@dataclass(frozen=True, slots=True)
class QueryIntent:
    kind: QueryKind


@dataclass(frozen=True, slots=True)
class HomeIntent:
    axis: HomeAxis


@dataclass(frozen=True, slots=True)
class SortIntent:
    slot: int

    def __post_init__(self) -> None:
        if isinstance(self.slot, bool) or not 0 <= self.slot <= 102:
            raise ValueError("slot must be an integer in range 0..102")


type WorkerIntent = QueryIntent | HomeIntent | SortIntent
type WorkerResult = Status | Capabilities | QueueSnapshot | Completion
_FutureValue = TypeVar("_FutureValue")


class SerialWorkerError(RuntimeError):
    """Base error for serial worker admission and lifecycle failures."""


class WorkerNotRunningError(SerialWorkerError):
    """The worker cannot admit work in its current lifecycle state."""


class QueueFullError(SerialWorkerError):
    """The bounded normal admission lane is full."""


class StopInProgressError(SerialWorkerError):
    """Normal admission is closed while priority stop is pending."""


class PreemptedByStopError(SerialWorkerError):
    """Work was invalidated by an admitted priority stop."""


class WorkerUncertainError(SerialWorkerError):
    """An active result cannot be trusted after a failed stop."""


class WorkerStartupError(SerialWorkerError):
    """The worker could not establish its initial v2 session."""


class WorkerShutdownTimeout(SerialWorkerError):
    """The worker thread did not stop within the caller's finite deadline."""


@dataclass(slots=True)
class _WorkItem:
    intent: WorkerIntent
    future: Future[WorkerResult]


_PRIORITY_STOP = object()


class SerialWorker:
    """Own one transport and one ProtocolClient on a dedicated thread."""

    def __init__(
        self,
        transport_factory: Callable[[], OwnedByteStream],
        *,
        normal_capacity: int = 16,
        protocol_timeout: float = 1.0,
        interrupt_poll_interval: float = 0.01,
    ) -> None:
        if normal_capacity < 1:
            raise ValueError("normal_capacity must be positive")
        if not isfinite(protocol_timeout) or protocol_timeout <= 0:
            raise ValueError("protocol_timeout must be finite and positive")
        if not isfinite(interrupt_poll_interval) or interrupt_poll_interval <= 0:
            raise ValueError("interrupt_poll_interval must be finite and positive")
        self._transport_factory = transport_factory
        self._normal_capacity = normal_capacity
        self._protocol_timeout = protocol_timeout
        self._interrupt_poll_interval = interrupt_poll_interval
        self._condition = threading.Condition()
        self._state = WorkerState.NEW
        self._normal: deque[_WorkItem] = deque()
        self._stop_requested = False
        self._stop_waiters: list[Future[None]] = []
        self._thread: threading.Thread | None = None
        self._worker_thread_id: int | None = None
        self._failure: Exception | None = None

    @property
    def state(self) -> WorkerState:
        with self._condition:
            return self._state

    @property
    def worker_thread_id(self) -> int | None:
        with self._condition:
            return self._worker_thread_id

    @property
    def failure(self) -> Exception | None:
        with self._condition:
            return self._failure

    def start(self, *, timeout: float = 5.0) -> None:
        self._validate_lifecycle_timeout(timeout)
        with self._condition:
            if self._state is WorkerState.NEW:
                self._state = WorkerState.STARTING
                self._thread = threading.Thread(
                    target=self._run,
                    name="cs71d-serial-worker",
                    daemon=True,
                )
                self._thread.start()
            elif self._state is WorkerState.RUNNING:
                return
            elif self._state is not WorkerState.STARTING:
                raise WorkerNotRunningError(f"cannot start worker in state {self._state}")

            ready = self._condition.wait_for(
                lambda: self._state is not WorkerState.STARTING,
                timeout,
            )
            if not ready:
                self._state = WorkerState.CLOSING
                self._condition.notify_all()
                raise WorkerStartupError("serial worker startup deadline expired")
            if self._state is not WorkerState.RUNNING:
                raise WorkerStartupError("serial worker startup failed") from self._failure

    def submit(self, intent: WorkerIntent) -> Future[WorkerResult]:
        if not isinstance(intent, QueryIntent | HomeIntent | SortIntent):
            raise TypeError("intent must be a closed serial worker intent")
        future: Future[WorkerResult] = Future()
        with self._condition:
            self._require_running()
            if self._stop_requested:
                raise StopInProgressError("priority stop is in progress")
            if len(self._normal) >= self._normal_capacity:
                raise QueueFullError("normal serial admission queue is full")
            self._normal.append(_WorkItem(intent, future))
            self._condition.notify_all()
        return future

    def submit_priority_stop(self) -> Future[None]:
        follower: Future[None] = Future()
        preempted: tuple[_WorkItem, ...] = ()
        with self._condition:
            self._require_running()
            self._stop_waiters.append(follower)
            if not self._stop_requested:
                self._stop_requested = True
                preempted = tuple(self._normal)
                self._normal.clear()
            self._condition.notify_all()
        for item in preempted:
            self._fail_pending_future(
                item.future,
                PreemptedByStopError("queued work invalidated by priority stop"),
            )
        return follower

    def close(self, *, timeout: float = 5.0) -> None:
        self._validate_lifecycle_timeout(timeout)
        preempted: tuple[_WorkItem, ...] = ()
        thread: threading.Thread | None
        with self._condition:
            if self._state is WorkerState.NEW:
                self._state = WorkerState.CLOSED
                return
            if self._state is WorkerState.CLOSED:
                return
            if self._state in {WorkerState.STARTING, WorkerState.RUNNING}:
                was_running = self._state is WorkerState.RUNNING
                self._state = WorkerState.CLOSING
                preempted = tuple(self._normal)
                self._normal.clear()
                if was_running:
                    self._stop_requested = True
                self._condition.notify_all()
            thread = self._thread
        for item in preempted:
            self._fail_pending_future(
                item.future,
                PreemptedByStopError("queued work invalidated by worker shutdown"),
            )
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise WorkerShutdownTimeout("serial worker shutdown deadline expired")

    def _run(self) -> None:
        transport: OwnedByteStream | None = None
        try:
            self._worker_thread_id = threading.get_ident()
            transport = self._transport_factory()
            if self._is_closing():
                return
            client = ProtocolClient(transport, timeout=self._protocol_timeout)
            client.wait_ready()
            client.v1_ping_barrier()
            if not client.activate():
                raise WorkerStartupError("controller does not provide protocol v2")
            with self._condition:
                if self._state is WorkerState.CLOSING:
                    return
                self._state = WorkerState.RUNNING
                self._condition.notify_all()

            while True:
                action = self._next_action()
                if action is None:
                    return
                if action is _PRIORITY_STOP:
                    self._execute_stop(client)
                else:
                    assert isinstance(action, _WorkItem)
                    self._execute_item(client, action)
        except Exception as exc:
            self._mark_failed(exc)
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception as exc:
                    self._mark_failed(exc)
            self._finish_thread()

    def _next_action(self) -> _WorkItem | object | None:
        with self._condition:
            self._condition.wait_for(
                lambda: (
                    self._stop_requested
                    or bool(self._normal)
                    or self._state in {WorkerState.CLOSING, WorkerState.FAILED}
                )
            )
            if self._stop_requested:
                return _PRIORITY_STOP
            if self._state in {WorkerState.CLOSING, WorkerState.FAILED}:
                return None
            return self._normal.popleft()

    def _execute_item(self, client: ProtocolClient, item: _WorkItem) -> None:
        if not item.future.set_running_or_notify_cancel():
            return
        try:
            result = self._dispatch(client, item.intent)
        except RequestInterruptedError:
            item.future.set_exception(
                PreemptedByStopError("active work cancelled by trusted priority stop")
            )
            self._resolve_stop(None)
            return
        except Exception as exc:
            if self._is_stop_requested():
                item.future.set_exception(
                    WorkerUncertainError("active work became uncertain during priority stop")
                )
                self._resolve_stop(exc)
                self._mark_failed(exc)
            else:
                item.future.set_exception(exc)
            return

        if self._is_stop_requested():
            try:
                client.out_of_band_stop()
            except Exception as exc:
                item.future.set_exception(
                    WorkerUncertainError("active result invalidated by failed priority stop")
                )
                self._resolve_stop(exc)
                self._mark_failed(exc)
            else:
                item.future.set_exception(
                    PreemptedByStopError("active result invalidated by trusted priority stop")
                )
                self._resolve_stop(None)
            return
        item.future.set_result(result)

    def _dispatch(self, client: ProtocolClient, intent: WorkerIntent) -> WorkerResult:
        if isinstance(intent, QueryIntent):
            if intent.kind is QueryKind.STATUS:
                return client.get_status()
            if intent.kind is QueryKind.CAPABILITIES:
                return client.get_capabilities()
            return client.get_queue()
        interrupt = self._is_stop_requested
        if isinstance(intent, HomeIntent):
            command = {
                HomeAxis.FEEDER: "homefeeder",
                HomeAxis.SORTER: "homesorter",
                HomeAxis.BOTH: "homeall",
            }[intent.axis]
        else:
            command = f"sortto:{intent.slot}"
        return client.request(
            command,
            interrupt_requested=interrupt,
            interrupt_poll_interval=self._interrupt_poll_interval,
        )

    def _execute_stop(self, client: ProtocolClient) -> None:
        try:
            client.out_of_band_stop()
        except Exception as exc:
            self._resolve_stop(exc)
            self._mark_failed(exc)
        else:
            self._resolve_stop(None)

    def _is_stop_requested(self) -> bool:
        with self._condition:
            return self._stop_requested

    def _is_closing(self) -> bool:
        with self._condition:
            return self._state is WorkerState.CLOSING

    def _resolve_stop(self, error: Exception | None) -> None:
        with self._condition:
            waiters = tuple(self._stop_waiters)
            self._stop_waiters.clear()
            self._stop_requested = False
            self._condition.notify_all()
        for waiter in waiters:
            if error is None:
                if waiter.set_running_or_notify_cancel():
                    waiter.set_result(None)
            else:
                self._fail_pending_future(waiter, error)

    def _mark_failed(self, error: Exception) -> None:
        with self._condition:
            if self._state not in {WorkerState.CLOSED, WorkerState.FAILED}:
                self._state = WorkerState.FAILED
                self._failure = error
                self._condition.notify_all()

    def _finish_thread(self) -> None:
        with self._condition:
            if self._state is not WorkerState.FAILED:
                self._state = WorkerState.CLOSED
            pending = tuple(self._normal)
            self._normal.clear()
            stop_waiters = tuple(self._stop_waiters)
            self._stop_waiters.clear()
            self._stop_requested = False
            self._condition.notify_all()
        terminal_error = self._failure or WorkerNotRunningError("serial worker stopped")
        for item in pending:
            self._fail_pending_future(item.future, terminal_error)
        for waiter in stop_waiters:
            self._fail_pending_future(waiter, terminal_error)

    def _require_running(self) -> None:
        if self._state is not WorkerState.RUNNING:
            raise WorkerNotRunningError(f"worker is not running: {self._state}")

    @staticmethod
    def _fail_pending_future(
        future: Future[_FutureValue],
        error: Exception,
    ) -> None:
        if future.set_running_or_notify_cancel():
            future.set_exception(error)

    @staticmethod
    def _validate_lifecycle_timeout(timeout: float) -> None:
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("lifecycle timeout must be finite and positive")

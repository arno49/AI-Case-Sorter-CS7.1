"""Single-owner protocol worker with bounded admission and priority stop."""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import TypeVar

from cs71_protocol import (
    Capabilities,
    Completion,
    ProtocolClient,
    ProtocolError,
    QueueSnapshot,
    RecoveryError,
    RequestInterruptedError,
    Status,
)

from .device import OwnedByteStream
from .session import ConnectionState, SessionSnapshot, SessionState


class WorkerState(StrEnum):
    """Lifecycle of the owning thread, not of the protocol session."""

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


class SessionNotReadyError(SerialWorkerError):
    """The protocol session is not verified, so new work is not admitted."""


class QueueFullError(SerialWorkerError):
    """The bounded normal admission lane is full."""


class StopInProgressError(SerialWorkerError):
    """Normal admission is closed while priority stop is pending."""


class PreemptedByStopError(SerialWorkerError):
    """Work was invalidated by an admitted priority stop."""


class PreemptedByRecoveryError(SerialWorkerError):
    """Work was discarded because the session broke; it is never replayed."""


class WorkerUncertainError(SerialWorkerError):
    """An active result cannot be trusted after a failed stop or recovery."""


class WorkerStartupError(SerialWorkerError):
    """The worker could not establish its initial v2 session."""


class WorkerShutdownTimeout(SerialWorkerError):
    """The worker thread did not stop within the caller's finite deadline."""


@dataclass(frozen=True, slots=True)
class SessionProfile:
    """The required snapshots gathered before a session is published READY.

    `runtime-and-domain.md` requires that ``READY`` means a verified session
    *and* that required snapshots exist. These are what the daemon validates
    against, so they are observed from the controller rather than assumed.
    """

    capabilities: Capabilities
    status: Status


@dataclass(slots=True)
class _WorkItem:
    intent: WorkerIntent
    future: Future[WorkerResult]
    on_dispatch: Callable[[], None] | None = None


@dataclass(slots=True)
class _Link:
    """The transport and client currently owned by the worker thread."""

    transport: OwnedByteStream
    client: ProtocolClient


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
        max_reconnect_attempts: int = 1,
        session_observer: Callable[[SessionSnapshot], None] | None = None,
        profile_observer: Callable[[SessionProfile], None] | None = None,
    ) -> None:
        if normal_capacity < 1:
            raise ValueError("normal_capacity must be positive")
        if not isfinite(protocol_timeout) or protocol_timeout <= 0:
            raise ValueError("protocol_timeout must be finite and positive")
        if not isfinite(interrupt_poll_interval) or interrupt_poll_interval <= 0:
            raise ValueError("interrupt_poll_interval must be finite and positive")
        if max_reconnect_attempts < 0:
            raise ValueError("max_reconnect_attempts must not be negative")
        self._transport_factory = transport_factory
        self._normal_capacity = normal_capacity
        self._protocol_timeout = protocol_timeout
        self._interrupt_poll_interval = interrupt_poll_interval
        self._max_reconnect_attempts = max_reconnect_attempts
        self._session = SessionState(observer=session_observer)
        self._profile_observer = profile_observer
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
    def session(self) -> SessionSnapshot:
        return self._session.snapshot

    @property
    def session_history(self) -> tuple[SessionSnapshot, ...]:
        return self._session.history

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

    def submit(
        self,
        intent: WorkerIntent,
        *,
        on_dispatch: Callable[[], None] | None = None,
    ) -> Future[WorkerResult]:
        """Admit one intent and return its future.

        ``on_dispatch`` runs on the worker thread immediately before the first
        byte of this intent is written, which is the only moment a caller can
        distinguish "queued" from "transmitted". Raising from it aborts the
        intent before transmission and fails the future with that exception, so
        a caller whose own preconditions expired can stop the command from
        reaching the controller. It must not call back into this worker.
        """
        if not isinstance(intent, QueryIntent | HomeIntent | SortIntent):
            raise TypeError("intent must be a closed serial worker intent")
        future: Future[WorkerResult] = Future()
        with self._condition:
            self._require_running()
            # Read the session while holding the admission lock. Recovery
            # publishes RECOVERING and only then drains the queue under this
            # same lock, so a submission either observes the new state and is
            # refused, or lands in the queue in time to be drained. It can
            # never survive unnoticed across a session break.
            snapshot = self._session.snapshot
            if not snapshot.admits_work:
                raise SessionNotReadyError(f"session is not ready: {snapshot.state}")
            if self._stop_requested:
                raise StopInProgressError("priority stop is in progress")
            if len(self._normal) >= self._normal_capacity:
                raise QueueFullError("normal serial admission queue is full")
            self._normal.append(_WorkItem(intent, future, on_dispatch))
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
        self._fail_items(
            preempted,
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
        self._fail_items(
            preempted,
            PreemptedByStopError("queued work invalidated by worker shutdown"),
        )
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
            if thread.is_alive():
                raise WorkerShutdownTimeout("serial worker shutdown deadline expired")

    def _run(self) -> None:
        link: _Link | None = None
        try:
            self._worker_thread_id = threading.get_ident()
            link = self._connect()
            if self._is_closing():
                return
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
                    self._execute_stop(link.client)
                    continue
                assert isinstance(action, _WorkItem)
                fault = self._execute_item(link.client, action)
                if fault is None:
                    continue
                link = self._recover(link, fault)
                if link is None:
                    return
        except Exception as exc:
            self._session.transition(ConnectionState.UNCERTAIN, f"worker fault: {exc}")
            self._mark_failed(exc)
        finally:
            if link is not None:
                self._close_transport(link.transport)
            self._finish_thread()

    def _connect(self) -> _Link:
        """Open a transport and walk a fresh controller up to a verified v2 session."""
        self._session.transition(ConnectionState.CONNECTING, "opening controller transport")
        transport = self._transport_factory()
        try:
            client = ProtocolClient(transport, timeout=self._protocol_timeout)
            link = _Link(transport, client)
            if self._is_closing():
                return link
            self._session.transition(ConnectionState.VERIFYING_V1, "verifying legacy v1 barrier")
            client.wait_ready()
            client.v1_ping_barrier()
            self._activate(client)
            return link
        except Exception:
            # The caller never receives the link, so this thread still owns the
            # transport and must release it here.
            self._close_transport(transport)
            raise

    def _activate(self, client: ProtocolClient) -> None:
        self._session.transition(ConnectionState.ACTIVATING_V2, "negotiating protocol v2")
        if not client.activate():
            raise WorkerStartupError("controller does not provide protocol v2")
        # Gather the required snapshots before publishing READY: what the
        # controller advertises is what the daemon validates commands against.
        self._publish_profile(client)
        self._session.transition(ConnectionState.READY, "verified v2 session")

    def _publish_profile(self, client: ProtocolClient) -> None:
        if self._profile_observer is None:
            return
        profile = SessionProfile(client.get_capabilities(), client.get_status())
        self._profile_observer(profile)

    def _recover(self, link: _Link, fault: Exception) -> _Link | None:
        """Return a usable link, or ``None`` when the session stays uncertain.

        In-session recovery belongs to `cs71_protocol`: an unsafe exchange has
        already run its stop/reset/verify sequence and reports the outcome as
        ``RecoveryError.recovered``. The worker only decides what to do next,
        and escalates to a full reconnect when v1 was not verified.

        Queued work is discarded rather than carried across the break: the
        snapshot generation has moved, so callers must re-admit against the new
        generation. An incomplete state-changing command is never re-sent.
        """
        self._session.transition(ConnectionState.RECOVERING, f"recovering after {fault}")
        self._drain_queue(
            PreemptedByRecoveryError("queued work discarded by session recovery; not replayed")
        )
        if self._is_closing():
            self._close_transport(link.transport)
            return None

        if isinstance(fault, RecoveryError) and fault.recovered:
            self._session.transition(
                ConnectionState.VERIFYING_V1, "v1 verified by protocol library recovery"
            )
            try:
                self._activate(link.client)
            except Exception as exc:
                return self._reconnect(link, exc)
            return link

        return self._reconnect(link, fault)

    def _reconnect(self, link: _Link, fault: Exception) -> _Link | None:
        """Replace a lost transport with a new one, starting from DISCONNECTED."""
        self._close_transport(link.transport)
        self._session.transition(ConnectionState.DISCONNECTED, f"transport lost: {fault}")
        for _attempt in range(self._max_reconnect_attempts):
            if self._is_closing():
                return None
            try:
                replacement = self._connect()
            except Exception as exc:
                self._session.transition(
                    ConnectionState.DISCONNECTED, f"reconnect attempt failed: {exc}"
                )
                continue
            if self._session.state is ConnectionState.READY:
                return replacement
            # Startup observed a shutdown request before the session verified.
            self._close_transport(replacement.transport)
            return None
        self._fail_session(fault, "controller could not be reconnected")
        return None

    def _fail_session(self, error: Exception, reason: str) -> None:
        recovered = isinstance(error, RecoveryError) and error.recovered
        detail = f"{reason}: {error}" + (" (verified v1 only)" if recovered else "")
        self._session.transition(ConnectionState.UNCERTAIN, detail)
        self._mark_failed(error)

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

    def _execute_item(self, client: ProtocolClient, item: _WorkItem) -> Exception | None:
        """Run one intent; return the fault that broke the session, if any."""
        if not item.future.set_running_or_notify_cancel():
            return None
        if item.on_dispatch is not None:
            try:
                item.on_dispatch()
            except Exception as exc:
                # The caller withdrew this intent before any byte was written,
                # so the session is untouched and nothing needs recovery.
                item.future.set_exception(exc)
                return None
        try:
            result = self._dispatch(client, item.intent)
        except RequestInterruptedError:
            item.future.set_exception(
                PreemptedByStopError("active work cancelled by trusted priority stop")
            )
            self._resolve_stop(None)
            return None
        except Exception as exc:
            if self._is_stop_requested():
                item.future.set_exception(
                    WorkerUncertainError("active work became uncertain during priority stop")
                )
                self._resolve_stop(exc)
                self._mark_failed(exc)
                return None
            item.future.set_exception(exc)
            return exc if isinstance(exc, ProtocolError | OSError) else None

        if self._is_stop_requested():
            try:
                client.out_of_band_stop()
            except Exception as exc:
                item.future.set_exception(
                    WorkerUncertainError("active result invalidated by failed priority stop")
                )
                self._resolve_stop(exc)
                self._fail_session(exc, "priority stop was unsafe")
            else:
                item.future.set_exception(
                    PreemptedByStopError("active result invalidated by trusted priority stop")
                )
                self._resolve_stop(None)
            return None
        item.future.set_result(result)
        if isinstance(item.intent, HomeIntent | SortIntent):
            # A completed movement changes what the controller will accept
            # next, so re-observe readiness instead of inferring it.
            try:
                self._publish_profile(client)
            except Exception as exc:
                return exc if isinstance(exc, ProtocolError | OSError) else None
        return None

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
            self._fail_session(exc, "priority stop was unsafe")
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

    def _drain_queue(self, error: Exception) -> None:
        with self._condition:
            pending = tuple(self._normal)
            self._normal.clear()
        self._fail_items(pending, error)

    def _close_transport(self, transport: OwnedByteStream) -> None:
        try:
            transport.close()
        except Exception as exc:
            self._mark_failed(exc)

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
        if self._session.state is not ConnectionState.UNCERTAIN:
            self._session.transition(ConnectionState.DISCONNECTED, "serial worker stopped")
        terminal_error = self._failure or WorkerNotRunningError("serial worker stopped")
        self._fail_items(pending, terminal_error)
        for waiter in stop_waiters:
            self._fail_pending_future(waiter, terminal_error)

    def _require_running(self) -> None:
        if self._state is not WorkerState.RUNNING:
            raise WorkerNotRunningError(f"worker is not running: {self._state}")

    @staticmethod
    def _fail_items(items: tuple[_WorkItem, ...], error: Exception) -> None:
        for item in items:
            SerialWorker._fail_pending_future(item.future, error)

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

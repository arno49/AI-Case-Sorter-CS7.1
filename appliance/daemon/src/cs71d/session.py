"""Observable connection session state for the single-owner serial worker.

Connection state describes *session confidence*, not physical safety, and is
deliberately separate from :class:`~cs71d.serial_worker.WorkerState`, which
describes the owning thread's lifecycle. A worker thread can be running while
its session is ``RECOVERING`` or ``UNCERTAIN``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum


class ConnectionState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    VERIFYING_V1 = "verifying_v1"
    ACTIVATING_V2 = "activating_v2"
    READY = "ready"
    RECOVERING = "recovering"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """An immutable published view of the session at one generation."""

    state: ConnectionState
    generation: int
    reason: str

    @property
    def admits_work(self) -> bool:
        """Only a verified, fully activated session may accept new work."""
        return self.state is ConnectionState.READY


class SessionState:
    """Publish connection transitions with a monotonic snapshot generation.

    Re-entering the current state is not a material change: it neither
    increments the generation nor replaces the published reason. This keeps the
    generation and the snapshot exactly coupled, so a caller holding
    generation *N* can trust that nothing material happened while it still
    observes *N*.
    """

    def __init__(
        self,
        *,
        observer: Callable[[SessionSnapshot], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._observer = observer
        self._snapshot = SessionSnapshot(ConnectionState.DISCONNECTED, 1, "initial")
        self._history: list[SessionSnapshot] = [self._snapshot]

    @property
    def snapshot(self) -> SessionSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def state(self) -> ConnectionState:
        with self._lock:
            return self._snapshot.state

    @property
    def generation(self) -> int:
        with self._lock:
            return self._snapshot.generation

    @property
    def history(self) -> tuple[SessionSnapshot, ...]:
        with self._lock:
            return tuple(self._history)

    def transition(self, state: ConnectionState, reason: str) -> SessionSnapshot:
        """Publish ``state`` and return the resulting snapshot."""
        with self._lock:
            if state is self._snapshot.state:
                return self._snapshot
            self._snapshot = SessionSnapshot(state, self._snapshot.generation + 1, reason)
            self._history.append(self._snapshot)
            published = self._snapshot
            observer = self._observer
        if observer is not None:
            observer(published)
        return published

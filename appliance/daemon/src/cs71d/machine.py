"""The immutable machine view and its monotonic snapshot generation.

The generation belongs to the *machine*, not to the serial session: any
material state, readiness or operation change advances it. :class:`SessionState`
contributes connection confidence into this view rather than owning the
version, so an operation transition and a connection transition move the same
counter and a caller holding generation *N* can trust that nothing material
happened while it still observes *N*.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace

from .session import ConnectionState, SessionSnapshot


@dataclass(frozen=True, slots=True)
class MachineSnapshot:
    """An immutable published view of the machine at one generation."""

    generation: int
    connection: ConnectionState
    reason: str
    active_operation_id: str | None = None

    @property
    def admits_work(self) -> bool:
        """Only a verified, fully activated session admits new machine work.

        Readiness is session confidence. It does not assert homing, safe
        surroundings, or permission to move.
        """
        return self.connection is ConnectionState.READY


class MachineState:
    """Publish the machine view under one monotonic generation."""

    def __init__(self) -> None:
        # Re-entrant because an admission decision publishes the transition it
        # just made durable without leaving the block that froze the view.
        self._lock = threading.RLock()
        self._snapshot = MachineSnapshot(1, ConnectionState.DISCONNECTED, "initial")
        self._history: list[MachineSnapshot] = [self._snapshot]

    @property
    def snapshot(self) -> MachineSnapshot:
        with self._lock:
            return self._snapshot

    @property
    def generation(self) -> int:
        with self._lock:
            return self._snapshot.generation

    @property
    def history(self) -> tuple[MachineSnapshot, ...]:
        with self._lock:
            return tuple(self._history)

    @contextmanager
    def admission(self) -> Iterator[MachineSnapshot]:
        """Hold the machine view still while one admission decision is made.

        A caller evaluating optimistic concurrency must see the same generation
        it validates against for the whole decision. Holding this while calling
        into the serial worker would invert the lock order the worker thread
        uses to publish transitions, so the decision is made and made durable
        here, and the enqueue happens after the block exits.
        """
        with self._lock:
            yield self._snapshot

    @contextmanager
    def transition(self, active_operation_id: str | None, reason: str) -> Iterator[int]:
        """Yield the generation a caller must make durable, then publish it.

        The caller writes its durable record inside the block using the yielded
        generation. If that write fails the block raises, nothing is published,
        and the machine view stays exactly as durable as the journal. The
        published version is therefore never ahead of the record it describes.
        """
        with self._lock:
            yield self._snapshot.generation + 1
            self._publish(
                replace(self._snapshot, active_operation_id=active_operation_id, reason=reason)
            )

    def observe_connection(self, session: SessionSnapshot) -> MachineSnapshot:
        """Fold a published connection transition into the machine view."""
        with self._lock:
            if session.state is self._snapshot.connection:
                return self._snapshot
            return self._publish(
                replace(self._snapshot, connection=session.state, reason=session.reason)
            )

    def _publish(self, candidate: MachineSnapshot) -> MachineSnapshot:
        published = replace(candidate, generation=self._snapshot.generation + 1)
        self._snapshot = published
        self._history.append(published)
        return published

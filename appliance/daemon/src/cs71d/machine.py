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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from .session import ConnectionState, SessionSnapshot


class FaultState(StrEnum):
    """Machine fault confidence, independent of session confidence."""

    CLEAR = "clear"
    ACTIVE = "active"
    LATCHED = "latched"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class FirmwareProfile:
    """What the controller advertises, as observed at activation.

    These are the limits the daemon validates commands against. They are read
    from the controller rather than configured, so a firmware build that
    advertises less cannot be asked for more.
    """

    protocol_version: int
    slot_max: int
    slot_count: int
    queue_depth: int
    feed_sensor: bool
    feed_home: bool
    sort_home: bool


@dataclass(frozen=True, slots=True)
class MachineReadiness:
    """The observed physical readiness the daemon must not assume."""

    feed_homed: bool
    sort_homed: bool
    fault_code: int
    mode: str
    phase: str


@dataclass(frozen=True, slots=True)
class MachineSnapshot:
    """An immutable published view of the machine at one generation."""

    generation: int
    connection: ConnectionState
    reason: str
    active_operation_id: str | None = None
    fault: FaultState = FaultState.CLEAR
    journal_available: bool = True
    firmware: FirmwareProfile | None = None
    readiness: MachineReadiness | None = None
    fault_id: str | None = None
    fault_opened_at: datetime | None = None

    @property
    def admits_work(self) -> bool:
        """Report whether new state-changing work may be admitted.

        This is session confidence plus durability, not permission to move: it
        does not assert homing or safe surroundings. A machine that cannot
        record what it is about to do does not admit new work, because an
        unjournaled command has no audit trail and no recoverable outcome.
        """
        return (
            self.connection is ConnectionState.READY
            and self.journal_available
            and self.fault is FaultState.CLEAR
        )


class MachineState:
    """Publish the machine view under one monotonic generation."""

    def __init__(
        self,
        *,
        observer: Callable[[MachineSnapshot], None] | None = None,
    ) -> None:
        # Re-entrant because an admission decision publishes the transition it
        # just made durable without leaving the block that froze the view.
        self._lock = threading.RLock()
        # Invoked for every published snapshot while the view is held still, so
        # observers see them in order. It must not block or re-enter the domain.
        self._observer = observer
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

    def observe_profile(
        self,
        firmware: FirmwareProfile,
        readiness: MachineReadiness,
    ) -> MachineSnapshot:
        """Fold observed controller capabilities and readiness into the view."""
        with self._lock:
            if (firmware, readiness) == (self._snapshot.firmware, self._snapshot.readiness):
                return self._snapshot
            return self._publish(
                replace(
                    self._snapshot,
                    firmware=firmware,
                    readiness=readiness,
                    reason="observed controller capabilities and readiness",
                )
            )

    def record_journal_fault(
        self,
        reason: str,
        *,
        fault_id: str | None = None,
        opened_at: datetime | None = None,
    ) -> MachineSnapshot:
        """Latch the machine as undurable after a failed journal operation.

        This does not clear on its own. Durability loss needs operator or
        service intervention, so the daemon stays not-ready until it is
        restarted against a healthy journal rather than quietly resuming
        control on an unrecorded path.
        """
        with self._lock:
            if not self._snapshot.journal_available:
                return self._snapshot
            return self._publish(
                replace(
                    self._snapshot,
                    journal_available=False,
                    fault=FaultState.LATCHED,
                    reason=reason,
                    fault_id=fault_id,
                    fault_opened_at=opened_at,
                )
            )

    def _publish(self, candidate: MachineSnapshot) -> MachineSnapshot:
        published = replace(candidate, generation=self._snapshot.generation + 1)
        self._snapshot = published
        self._history.append(published)
        if self._observer is not None:
            self._observer(published)
        return published

"""The bounded daemon event ring and its subscribers.

Publishing must never block. Events are produced on the serial worker thread —
the same thread that has to keep answering a priority stop — so a subscriber
that has stopped reading is dropped from the stream rather than allowed to
apply backpressure to the machine.

Loss is always explicit. A subscriber that falls behind its bounded queue, or
resumes from a cursor the ring no longer retains, is told to reconcile from a
snapshot; it is never quietly handed an incomplete sequence.

The daemon ``event_id`` is monotonic within the daemon's retention scope and is
not a protocol ``request_id``: it does not wrap and it is not session-scoped.
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

SNAPSHOT_REQUIRED = "snapshot.required"
HEARTBEAT = "heartbeat"
DEFAULT_RETENTION = 5_000
DEFAULT_SUBSCRIBER_CAPACITY = 256


@dataclass(frozen=True, slots=True)
class DaemonEvent:
    """One published daemon event, addressable by its monotonic cursor."""

    event_id: int
    type: str
    occurred_at: datetime
    generation: int
    operation_id: str | None = None
    data: Mapping[str, Any] = field(default_factory=dict)


class Overflowed(Exception):
    """A subscriber fell behind, so its stream can no longer be complete."""


class EventRing:
    """Retain a bounded window of events and fan them out to subscribers."""

    def __init__(
        self,
        *,
        retention: int = DEFAULT_RETENTION,
        subscriber_capacity: int = DEFAULT_SUBSCRIBER_CAPACITY,
    ) -> None:
        if retention < 1 or subscriber_capacity < 1:
            raise ValueError("retention and subscriber capacity must be positive")
        self._lock = threading.Lock()
        self._retained: deque[DaemonEvent] = deque(maxlen=retention)
        self._subscriber_capacity = subscriber_capacity
        self._subscribers: set[_Subscriber] = set()
        self._next_id = 1

    @property
    def retained(self) -> tuple[DaemonEvent, ...]:
        with self._lock:
            return tuple(self._retained)

    def publish(
        self,
        event_type: str,
        *,
        generation: int,
        occurred_at: datetime,
        operation_id: str | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> DaemonEvent:
        """Append one event and hand it to every subscriber without blocking."""
        with self._lock:
            event = DaemonEvent(
                event_id=self._next_id,
                type=event_type,
                occurred_at=occurred_at,
                generation=generation,
                operation_id=operation_id,
                data=dict(data or {}),
            )
            self._next_id += 1
            self._retained.append(event)
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            subscriber.offer(event)
        return event

    def subscribe(self, *, after: int | None = None) -> _Subscriber:
        """Attach a subscriber, optionally resuming after a retained cursor.

        A cursor the ring no longer retains cannot be resumed from without
        silently skipping events, so the subscriber is created already
        overflowed and will be told to reconcile from a snapshot.
        """
        with self._lock:
            subscriber = _Subscriber(self, capacity=self._subscriber_capacity)
            if after is not None:
                backlog = [event for event in self._retained if event.event_id > after]
                oldest = self._retained[0].event_id if self._retained else self._next_id
                # A cursor ahead of this ring belongs to a previous daemon
                # life, whose ids this one is about to reissue. Resuming would
                # hand the consumer ids that go backwards.
                if after >= self._next_id or after + 1 < oldest:
                    subscriber.mark_overflowed()
                else:
                    subscriber.prime(backlog)
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


class _Subscriber:
    """One bounded, non-blocking view of the ring."""

    def __init__(self, ring: EventRing, *, capacity: int) -> None:
        self._ring = ring
        self._condition = threading.Condition()
        self._pending: deque[DaemonEvent] = deque()
        self._capacity = capacity
        self._overflowed = False
        self._closed = False

    def offer(self, event: DaemonEvent) -> None:
        """Queue one event, or mark this subscriber lost if it cannot keep up."""
        with self._condition:
            if self._closed:
                return
            if len(self._pending) >= self._capacity:
                # Dropping the subscriber is the only option that neither
                # blocks the publisher nor hides the gap from the consumer.
                self._pending.clear()
                self._overflowed = True
            else:
                self._pending.append(event)
            self._condition.notify_all()

    def prime(self, events: list[DaemonEvent]) -> None:
        with self._condition:
            if len(events) > self._capacity:
                self._overflowed = True
                return
            self._pending.extend(events)

    def mark_overflowed(self) -> None:
        with self._condition:
            self._overflowed = True
            self._pending.clear()
            self._condition.notify_all()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._ring.unsubscribe(self)

    @property
    def overflowed(self) -> bool:
        with self._condition:
            return self._overflowed

    def drain(self, *, timeout: float) -> list[DaemonEvent]:
        """Return the events waiting for this subscriber, waiting up to ``timeout``.

        An empty result means the caller should emit a heartbeat: it is idle,
        not disconnected.
        """
        with self._condition:
            self._condition.wait_for(
                lambda: bool(self._pending) or self._overflowed or self._closed,
                timeout,
            )
            if self._overflowed or self._closed:
                return []
            drained = list(self._pending)
            self._pending.clear()
            return drained

    def __enter__(self) -> _Subscriber:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __iter__(self) -> Iterator[DaemonEvent]:  # pragma: no cover - convenience only
        while not self._closed:
            yield from self.drain(timeout=1.0)

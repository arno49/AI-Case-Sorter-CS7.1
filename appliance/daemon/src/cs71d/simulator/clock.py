"""Explicit deterministic clock and callback scheduler."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(order=True, slots=True)
class _ScheduledCall:
    due_ms: int
    sequence: int
    callback: Callable[[], None] = field(compare=False)


class ManualClock:
    """Advance scheduled simulator work only when a test explicitly requests it."""

    def __init__(self) -> None:
        self._now_ms = 0
        self._sequence = 0
        self._scheduled: list[_ScheduledCall] = []

    @property
    def now_ms(self) -> int:
        return self._now_ms

    @property
    def pending_count(self) -> int:
        return len(self._scheduled)

    def call_later(self, delay_ms: int, callback: Callable[[], None]) -> None:
        if delay_ms < 0:
            raise ValueError("delay_ms must be non-negative")
        self._sequence += 1
        heapq.heappush(
            self._scheduled,
            _ScheduledCall(self._now_ms + delay_ms, self._sequence, callback),
        )

    def advance(self, delta_ms: int) -> None:
        if delta_ms < 0:
            raise ValueError("delta_ms must be non-negative")
        target_ms = self._now_ms + delta_ms
        while self._scheduled and self._scheduled[0].due_ms <= target_ms:
            scheduled = heapq.heappop(self._scheduled)
            self._now_ms = scheduled.due_ms
            scheduled.callback()
        self._now_ms = target_ms

    def clear(self) -> None:
        self._scheduled.clear()

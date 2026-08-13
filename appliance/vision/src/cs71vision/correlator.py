"""Turn `cs71d`'s confirmed sort outcomes into labeled dataset examples, and
match each one against the read-only suggestion (if any) that was live when
it happened (PI-VISION-006).

`cs71d` is the source of truth for "what actually happened" - a frame is
only ever attached to a label once `cs71d` has recorded a trusted terminal
for the sort operation, never guessed at from the frame alone. This module
owns the correlation; `dataset.DatasetStore` owns storage and
`daemon_client.DaemonClient` owns reading `cs71d`.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from typing import Protocol

from .camera import Frame
from .daemon_client import DaemonClientError, SortSuccess
from .dataset import DatasetStore

_LOGGER = logging.getLogger("cs71vision.correlator")


class SortSuccessSource(Protocol):
    """What the correlator needs from a daemon client - `DaemonClient` today."""

    def recent_sort_successes(self, *, limit: int = 25) -> tuple[SortSuccess, ...]: ...


class FrameBuffer:
    """The last `capacity` captured frames, for looking back through.

    Reading does not remove entries: more than one poll may need to look at
    the same window, and a slow-to-succeed operation should still find the
    frame captured near its own admission, not just its most recent one.
    """

    def __init__(self, capacity: int = 64) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._frames: deque[Frame] = deque(maxlen=capacity)

    def add(self, frame: Frame) -> None:
        self._frames.append(frame)

    def closest_at_or_before(self, moment: datetime) -> Frame | None:
        """Return the most recently captured frame at or before `moment`.

        `None` when nothing in the buffer qualifies - the buffer emptied,
        every frame is newer than `moment`, or nothing has been captured
        yet. The caller must treat that as "no evidence", not guess.
        """
        candidate: Frame | None = None
        for frame in self._frames:
            if frame.captured_at <= moment and (
                candidate is None or frame.captured_at > candidate.captured_at
            ):
                candidate = frame
        return candidate

    def latest(self) -> Frame | None:
        """The most recently captured frame, or None if nothing has been captured yet."""
        return self._frames[-1] if self._frames else None


class Correlator:
    """Poll `cs71d` for newly succeeded sorts and label what can be labeled."""

    def __init__(self, client: SortSuccessSource, store: DatasetStore, buffer: FrameBuffer) -> None:
        self._client = client
        self._store = store
        self._buffer = buffer

    def poll_once(self) -> int:
        """Return how many new examples were recorded this call."""
        try:
            successes = self._client.recent_sort_successes()
        except DaemonClientError:
            _LOGGER.exception("cs71-vision could not reach cs71d to poll sort outcomes")
            return 0

        recorded = 0
        for success in successes:
            if self._store.has_example(success.operation_id):
                continue
            try:
                created_at = datetime.fromisoformat(success.created_at)
            except ValueError:
                _LOGGER.warning(
                    "sort operation %s has an unparseable created_at %r; discarding",
                    success.operation_id,
                    success.created_at,
                )
                continue
            frame = self._buffer.closest_at_or_before(created_at)
            if frame is None:
                _LOGGER.warning(
                    "no captured frame found for sort operation %s; discarding, not guessing",
                    success.operation_id,
                )
                continue
            if self._store.record_example(
                operation_id=success.operation_id,
                slot=success.slot,
                frame_png=frame.png,
                frame_captured_at=frame.captured_at,
                operation_created_at=created_at,
            ):
                recorded += 1
                self._match_suggestion(success.operation_id, success.slot, created_at)
        return recorded

    def _match_suggestion(self, operation_id: str, actual_slot: int, created_at: datetime) -> None:
        """Record what actually happened for whatever suggestion was live then.

        Silently does nothing when no suggestion qualifies - no active model
        yet, or nothing suggested before this sort - the same "discard,
        never guess" posture `poll_once` already applies to a missing frame.
        """
        suggestion = self._store.unmatched_suggestion_at_or_before(created_at)
        if suggestion is None:
            return
        self._store.record_suggestion_outcome(
            suggestion_id=suggestion.suggestion_id,
            operation_id=operation_id,
            actual_slot=actual_slot,
            recorded_at=created_at,
        )

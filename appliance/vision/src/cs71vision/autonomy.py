"""Confidence-gated autonomous sort (PI-VISION-008, ADR-0013).

Composes two independent gates, in a fixed order that cannot be reordered
away: the primer-presence axis (PI-VISION-010, SAF-09) always decides first
and unconditionally, then, only if it is confidently clear, a per-class
manufacturer-classification confidence threshold. `Autonomist` is the one
thing that ever calls `DaemonClient.submit_sort` - the same
separation-of-concerns `FrameSuggester`/`Correlator` already keep from their
own loops, so this module, not `classifier.py`, is the only place an
autonomous `cs71d` command can originate from.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

from .classifier import Suggestion
from .daemon_client import DaemonClientError
from .dataset import DatasetStore
from .primer import requires_operator_confirmation

_LOGGER = logging.getLogger("cs71vision.autonomy")


class SortSubmitter(Protocol):
    """What the autonomist needs from a daemon client - `DaemonClient` today."""

    def current_generation(self) -> int: ...

    def submit_sort(self, *, slot: int, generation: int, idempotency_key: str) -> str: ...


def may_autonomously_sort(suggestion: Suggestion, thresholds: Mapping[int, float]) -> bool:
    """Whether `suggestion` may be submitted as an autonomous sort, right now.

    Primer is checked first and unconditionally (SAF-09): no per-class
    threshold, however high, ever overrides it - this mirrors
    `_commanding_actor`'s own fixed-order checks in `cs71d.api`, where the
    identity/role binding is never something a later check could relax.
    A class absent from `thresholds` can never autonomously sort -
    conservative by omission, not a low default number (ADR-0013: "starting
    conservative... mostly manual").
    """
    if requires_operator_confirmation(suggestion.primer_present):
        return False
    threshold = thresholds.get(suggestion.slot)
    if threshold is None:
        return False
    return suggestion.confidence >= threshold


class Autonomist:
    """Decide, and if warranted act on, the latest suggestion - once per tick.

    The one thing `runtime.AutonomyLoop` calls each tick, the same
    separation `FrameSuggester`/`runtime.SuggestionLoop` already keep. Never
    submits twice for the same suggestion (`DatasetStore.autonomous_attempt_exists`
    is checked first), and a `cs71d` rejection (stale generation, refused
    idempotency key, an unreachable socket) is logged and left for the next
    tick to reconsider against a then-current suggestion, never retried
    blindly within the same tick.
    """

    def __init__(
        self,
        store: DatasetStore,
        daemon_client: SortSubmitter,
        thresholds: Mapping[int, float],
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._daemon_client = daemon_client
        self._thresholds = thresholds
        self._now = now

    def attempt_once(self) -> int:
        """Return 1 if an autonomous sort was submitted, 0 otherwise.

        0 covers every reason nothing was submitted - no suggestion yet,
        already attempted, below threshold, primer not confidently clear, or
        `cs71d` refused it - the caller does not need to distinguish them.
        """
        suggestion_record = self._store.latest_suggestion()
        if suggestion_record is None:
            return 0
        if self._store.autonomous_attempt_exists(suggestion_record.suggestion_id):
            return 0
        suggestion = Suggestion(
            slot=suggestion_record.suggested_slot,
            confidence=suggestion_record.confidence,
            primer_present=suggestion_record.primer_present,
        )
        if not may_autonomously_sort(suggestion, self._thresholds):
            return 0
        try:
            generation = self._daemon_client.current_generation()
            operation_id = self._daemon_client.submit_sort(
                slot=suggestion.slot,
                generation=generation,
                idempotency_key=_idempotency_key(suggestion_record.suggestion_id),
            )
        except DaemonClientError:
            _LOGGER.exception("cs71-vision could not submit an autonomous sort")
            return 0
        self._store.record_autonomous_attempt(
            suggestion_id=suggestion_record.suggestion_id,
            operation_id=operation_id,
            slot=suggestion.slot,
            attempted_at=self._now(),
        )
        return 1


def _idempotency_key(suggestion_id: int) -> str:
    """Deterministic per suggestion, so a resubmission is the same command.

    Matches this contract's own idempotency guarantee
    (`docs/architecture/api-and-events.md`): if a tick somehow ran twice for
    the same not-yet-recorded suggestion, `cs71d` itself collapses the two
    requests into one operation rather than sorting the same case twice.
    """
    return f"autonomous-sort-{suggestion_id}"

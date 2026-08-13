"""Configurable routing profiles (PI-VISION-009, ADR-0013).

Several dozen manufacturer classes do not fit a handful of physical chutes,
and different runs may want a different mapping. This module is the one
place that decision lives, as operator-selectable configuration rather than
one hardcoded strategy - the same reasoning ADR-0013 already gives for not
choosing a single routing scheme up front.

Nothing else in this codebase has ever distinguished "manufacturer class"
from "physical chute" before this task: `classifier.classify_frame`'s own
output and every stored `suggested_slot`/`example.slot` are the same
integer the operator's own manual sort actually reached
(`dataset.py`'s own "never guessed at from a frame alone" contract). This
module treats the classifier's raw output as a class label and introduces
the missing indirection - `RoutingSession.route(class_id)` - between that
label and the chute a suggestion or an autonomous sort actually targets.
When no run is active, routing is a no-op identity function, so nothing
here changes today's behaviour unless an operator explicitly starts one.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

#: Mirrors `cs71d.api.MAX_API_SLOT` - a chute the daemon could never accept
#: would be dead configuration, never reachable.
MAX_SLOT = 63


class RoutingError(RuntimeError):
    """A routing profile or run request violates an appliance invariant."""


@dataclass(frozen=True, slots=True)
class FixedMapProfile:
    """A fixed class-to-chute map, plus exactly one overflow chute for
    every class the map does not name.
    """

    class_to_slot: Mapping[int, int]
    overflow_slot: int

    @property
    def kind(self) -> Literal["fixed"]:
        return "fixed"


@dataclass(frozen=True, slots=True)
class DynamicProfile:
    """The first N distinct classes seen in this run claim a chute, in the
    order given here - N being however many chutes this run has available.
    """

    available_slots: tuple[int, ...]

    @property
    def kind(self) -> Literal["dynamic"]:
        return "dynamic"


@dataclass(frozen=True, slots=True)
class TwoPassProfile:
    """A coarse class-to-group-chute map, the same shape as `FixedMapProfile`.

    `source_group` names which prior pass's output chute this run refines,
    for display/audit only - PI-VISION-009 does not enforce that a second
    pass's physical input actually came from that chute, the same way
    nothing in this codebase enforces that a manual sort's frame matches
    its slot; that remains a physical-world fact this software records
    rather than verifies. `None` means this is itself a first pass.
    """

    class_to_slot: Mapping[int, int]
    overflow_slot: int
    source_group: int | None = None

    @property
    def kind(self) -> Literal["two_pass"]:
        return "two_pass"


RoutingProfile = FixedMapProfile | DynamicProfile | TwoPassProfile


@dataclass(frozen=True, slots=True)
class LegendEntry:
    """One row of the chute<->class legend.

    `class_id` is `None` only for the one overflow entry a fixed/two-pass
    profile carries - "everything not named above", not a claim about a
    specific class. A dynamic profile's not-yet-claimed chutes are absent
    entirely, never listed with a fabricated class.
    """

    slot: int
    class_id: int | None
    overflow: bool


def validate_profile(profile: RoutingProfile) -> None:
    """Reject a profile that could never route correctly, before a run starts."""
    if isinstance(profile, FixedMapProfile | TwoPassProfile):
        if not profile.class_to_slot:
            raise RoutingError("a fixed or two-pass profile needs at least one mapped class")
        for class_id, slot in profile.class_to_slot.items():
            if not 0 <= class_id <= MAX_SLOT:
                raise RoutingError(f"class {class_id} is out of range")
            if not 0 <= slot <= MAX_SLOT:
                raise RoutingError(f"slot {slot} is out of range")
        if not 0 <= profile.overflow_slot <= MAX_SLOT:
            raise RoutingError(f"overflow_slot {profile.overflow_slot} is out of range")
        if profile.overflow_slot in profile.class_to_slot.values():
            raise RoutingError("overflow_slot must not also be an explicitly mapped chute")
        if (
            isinstance(profile, TwoPassProfile)
            and profile.source_group is not None
            and not 0 <= profile.source_group <= MAX_SLOT
        ):
            raise RoutingError(f"source_group {profile.source_group} is out of range")
    elif isinstance(profile, DynamicProfile):
        if not profile.available_slots:
            raise RoutingError("a dynamic profile needs at least one available chute")
        for slot in profile.available_slots:
            if not 0 <= slot <= MAX_SLOT:
                raise RoutingError(f"slot {slot} is out of range")
        if len(set(profile.available_slots)) != len(profile.available_slots):
            raise RoutingError("available_slots must not repeat a chute")
    else:
        raise RoutingError(f"unknown routing profile kind: {profile!r}")


def _legend_for(profile: RoutingProfile, claimed: Mapping[int, int]) -> tuple[LegendEntry, ...]:
    """`claimed` is slot -> class_id, already-assigned chutes only."""
    if isinstance(profile, DynamicProfile):
        return tuple(
            LegendEntry(slot=slot, class_id=class_id, overflow=False)
            for slot, class_id in claimed.items()
        )
    entries = [
        LegendEntry(slot=slot, class_id=class_id, overflow=False)
        for class_id, slot in profile.class_to_slot.items()
    ]
    entries.append(LegendEntry(slot=profile.overflow_slot, class_id=None, overflow=True))
    return tuple(sorted(entries, key=lambda entry: entry.slot))


@dataclass(frozen=True, slots=True)
class RoutingSnapshot:
    """What `GET /v1/routing` reports - `None` fields mean no run is active."""

    active: bool
    profile: RoutingProfile | None
    started_at: str | None
    legend: tuple[LegendEntry, ...]


class RoutingSession:
    """The one active run's routing state, or none.

    Shared between the suggestion loop (`route`, on its own thread) and the
    api server (`start`/`stop`/`snapshot`, on request-handling threads) -
    the same "one shared, lock-guarded object" shape `DatasetStore` already
    uses for its own cross-thread access.
    """

    def __init__(self, *, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._lock = threading.Lock()
        self._profile: RoutingProfile | None = None
        self._started_at: datetime | None = None
        self._claimed: dict[int, int] = {}
        self._now = now

    def start(self, profile: RoutingProfile) -> None:
        validate_profile(profile)
        with self._lock:
            self._profile = profile
            self._started_at = self._now()
            self._claimed = {}

    def stop(self) -> None:
        with self._lock:
            self._profile = None
            self._started_at = None
            self._claimed = {}

    def route(self, class_id: int) -> int:
        """The chute `class_id` belongs to right now.

        Identity (returns `class_id` unchanged) whenever no run is active -
        the same behaviour this codebase already had before this module
        existed, so nothing changes for an installation that never starts
        a routing run.
        """
        with self._lock:
            profile = self._profile
            if profile is None:
                return class_id
            if isinstance(profile, DynamicProfile):
                return self._route_dynamic(profile, class_id)
            return profile.class_to_slot.get(class_id, profile.overflow_slot)

    def _route_dynamic(self, profile: DynamicProfile, class_id: int) -> int:
        for slot, seen_class in self._claimed.items():
            if seen_class == class_id:
                return slot
        if len(self._claimed) >= len(profile.available_slots):
            raise RoutingError("every chute in this run has already been claimed by another class")
        slot = profile.available_slots[len(self._claimed)]
        self._claimed[slot] = class_id
        return slot

    def snapshot(self) -> RoutingSnapshot:
        with self._lock:
            if self._profile is None:
                return RoutingSnapshot(active=False, profile=None, started_at=None, legend=())
            return RoutingSnapshot(
                active=True,
                profile=self._profile,
                started_at=self._started_at.isoformat() if self._started_at else None,
                legend=_legend_for(self._profile, self._claimed),
            )

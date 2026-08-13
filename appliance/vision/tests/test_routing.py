from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cs71vision.routing import (
    DynamicProfile,
    FixedMapProfile,
    RoutingError,
    RoutingSession,
    TwoPassProfile,
    validate_profile,
)

START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def test_route_is_identity_when_no_run_is_active() -> None:
    session = RoutingSession(now=lambda: START)

    assert session.route(5) == 5
    assert session.snapshot().active is False
    assert session.snapshot().legend == ()


def test_fixed_map_routes_a_named_class_to_its_configured_chute() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(FixedMapProfile(class_to_slot={12: 3, 45: 5}, overflow_slot=7))

    assert session.route(12) == 3
    assert session.route(45) == 5


def test_fixed_map_routes_an_unnamed_class_to_the_overflow_chute() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(FixedMapProfile(class_to_slot={12: 3}, overflow_slot=7))

    assert session.route(99) == 7


def test_fixed_map_legend_lists_every_mapped_class_and_one_overflow_entry() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(FixedMapProfile(class_to_slot={12: 3, 45: 5}, overflow_slot=7))

    legend = session.snapshot().legend

    assert len(legend) == 3
    assert {(entry.slot, entry.class_id, entry.overflow) for entry in legend} == {
        (3, 12, False),
        (5, 45, False),
        (7, None, True),
    }


def test_dynamic_profile_assigns_the_first_distinct_classes_seen_in_order() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(DynamicProfile(available_slots=(1, 2, 3)))

    assert session.route(50) == 1
    assert session.route(60) == 2
    assert session.route(50) == 1  # already claimed - same chute, not a new one


def test_dynamic_profile_legend_only_lists_claimed_chutes() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(DynamicProfile(available_slots=(1, 2, 3)))
    session.route(50)

    legend = session.snapshot().legend

    assert len(legend) == 1
    assert legend[0].slot == 1
    assert legend[0].class_id == 50
    assert legend[0].overflow is False


def test_dynamic_profile_refuses_a_class_once_every_chute_is_claimed() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(DynamicProfile(available_slots=(1, 2)))
    session.route(50)
    session.route(60)

    with pytest.raises(RoutingError, match="already been claimed"):
        session.route(70)


def test_two_pass_routes_like_a_fixed_map_and_carries_its_source_group() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(TwoPassProfile(class_to_slot={12: 1, 45: 2}, overflow_slot=3, source_group=7))

    assert session.route(12) == 1
    assert session.snapshot().profile.source_group == 7  # type: ignore[union-attr]


def test_stop_clears_the_active_run() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(FixedMapProfile(class_to_slot={12: 3}, overflow_slot=7))

    session.stop()

    assert session.snapshot().active is False
    assert session.route(12) == 12  # identity again, not the old mapping


def test_starting_a_new_run_replaces_the_previous_one_entirely() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(DynamicProfile(available_slots=(1, 2)))
    session.route(50)

    session.start(FixedMapProfile(class_to_slot={50: 9}, overflow_slot=0))

    assert session.route(50) == 9  # not the stale dynamic assignment (slot 1)


def test_snapshot_reports_the_start_time_of_the_active_run() -> None:
    session = RoutingSession(now=lambda: START)
    session.start(FixedMapProfile(class_to_slot={12: 3}, overflow_slot=7))

    assert session.snapshot().started_at == START.isoformat()


class TestValidateProfile:
    def test_refuses_a_fixed_map_with_no_classes(self) -> None:
        with pytest.raises(RoutingError, match="at least one mapped class"):
            validate_profile(FixedMapProfile(class_to_slot={}, overflow_slot=0))

    def test_refuses_an_overflow_slot_that_is_also_explicitly_mapped(self) -> None:
        with pytest.raises(RoutingError, match="must not also be an explicitly mapped"):
            validate_profile(FixedMapProfile(class_to_slot={12: 3}, overflow_slot=3))

    def test_refuses_a_class_or_slot_out_of_range(self) -> None:
        with pytest.raises(RoutingError, match="out of range"):
            validate_profile(FixedMapProfile(class_to_slot={12: 999}, overflow_slot=0))
        with pytest.raises(RoutingError, match="out of range"):
            validate_profile(FixedMapProfile(class_to_slot={999: 3}, overflow_slot=0))

    def test_refuses_a_dynamic_profile_with_no_available_slots(self) -> None:
        with pytest.raises(RoutingError, match="at least one available chute"):
            validate_profile(DynamicProfile(available_slots=()))

    def test_refuses_a_dynamic_profile_with_a_repeated_slot(self) -> None:
        with pytest.raises(RoutingError, match="must not repeat"):
            validate_profile(DynamicProfile(available_slots=(1, 1)))

    def test_refuses_an_out_of_range_source_group(self) -> None:
        with pytest.raises(RoutingError, match="out of range"):
            validate_profile(
                TwoPassProfile(class_to_slot={1: 2}, overflow_slot=0, source_group=999)
            )

    def test_starting_an_invalid_profile_never_replaces_the_active_run(self) -> None:
        session = RoutingSession(now=lambda: START)
        session.start(FixedMapProfile(class_to_slot={12: 3}, overflow_slot=7))

        with pytest.raises(RoutingError):
            session.start(FixedMapProfile(class_to_slot={}, overflow_slot=0))

        assert session.route(12) == 3

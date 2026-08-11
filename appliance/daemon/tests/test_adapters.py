from __future__ import annotations

from typing import Any

import pytest

from cs71d.adapters import FEED_LIFECYCLE_GATE, intent_for, require_supported
from cs71d.machine import FirmwareProfile, MachineReadiness, MachineSnapshot
from cs71d.operations import (
    NotReadyError,
    OperationAction,
    UnsupportedOperationError,
    ValidationError,
)
from cs71d.serial_worker import HomeAxis, HomeIntent, QueryIntent, QueryKind, SortIntent
from cs71d.session import ConnectionState

FIRMWARE = FirmwareProfile(
    protocol_version=2,
    slot_max=102,
    slot_count=8,
    queue_depth=2,
    feed_sensor=True,
    feed_home=True,
    sort_home=True,
)
READY = MachineReadiness(
    feed_homed=True,
    sort_homed=True,
    fault_code=0,
    mode="running",
    phase="idle",
)


def _view(
    *,
    firmware: FirmwareProfile | None = FIRMWARE,
    readiness: MachineReadiness | None = READY,
) -> MachineSnapshot:
    return MachineSnapshot(
        generation=7,
        connection=ConnectionState.READY,
        reason="verified v2 session",
        firmware=firmware,
        readiness=readiness,
    )


@pytest.mark.parametrize(
    ("axis", "expected"),
    [
        ("feeder", HomeAxis.FEEDER),
        ("sorter", HomeAxis.SORTER),
        ("both", HomeAxis.BOTH),
    ],
)
def test_home_accepts_exactly_the_three_advertised_axes(axis: str, expected: HomeAxis) -> None:
    assert intent_for(OperationAction.HOME, {"axis": axis}) == HomeIntent(expected)


@pytest.mark.parametrize(
    "body",
    [
        {"axis": "carriage"},
        {"axis": "BOTH"},
        {"axis": 1},
        {"axis": "both", "slot": 3},
        {},
        {"slot": 3},
    ],
)
def test_home_refuses_anything_else(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        intent_for(OperationAction.HOME, body)


@pytest.mark.parametrize(
    "axis",
    ["both\nstop", "both;stop", "both 3", "sortto:3"],
)
def test_no_request_body_can_smuggle_a_protocol_payload(axis: str) -> None:
    """The axis is a closed vocabulary, so raw command text cannot pass through."""
    with pytest.raises(ValidationError):
        intent_for(OperationAction.HOME, {"axis": axis})


@pytest.mark.parametrize("body", [{"slot": True}, {"slot": "3"}, {"slot": 103}, {"slot": -1}, {}])
def test_sort_refuses_a_slot_that_is_not_a_protocol_slot(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        intent_for(OperationAction.SORT, body)


def test_sort_builds_a_closed_intent() -> None:
    assert intent_for(OperationAction.SORT, {"slot": 3}) == SortIntent(3)


def test_feed_is_refused_while_its_firmware_gate_is_open() -> None:
    with pytest.raises(UnsupportedOperationError, match=FEED_LIFECYCLE_GATE) as raised:
        intent_for(OperationAction.FEED, {"slot": 3})

    assert raised.value.code == "UNSUPPORTED"


def test_a_malformed_feed_request_is_a_validation_failure_before_the_gate() -> None:
    with pytest.raises(ValidationError):
        intent_for(OperationAction.FEED, {"axis": "both"})


def test_stop_is_not_admitted_through_the_ordinary_path() -> None:
    with pytest.raises(ValidationError, match="priority stop"):
        intent_for(OperationAction.STOP, {})


def test_capabilities_must_be_observed_before_anything_is_supported() -> None:
    with pytest.raises(NotReadyError, match="have not been observed"):
        require_supported(SortIntent(3), _view(firmware=None))
    with pytest.raises(NotReadyError, match="have not been observed"):
        require_supported(SortIntent(3), _view(readiness=None))


def test_a_slot_beyond_the_advertised_maximum_is_refused() -> None:
    view = _view(firmware=FirmwareProfile(2, 8, 8, 2, True, True, True))

    require_supported(SortIntent(8), view)
    with pytest.raises(ValidationError, match="advertised maximum 8"):
        require_supported(SortIntent(9), view)


def test_sorting_requires_a_known_sorter_position() -> None:
    unhomed = _view(readiness=MachineReadiness(True, False, 0, "recovering", "idle"))

    with pytest.raises(NotReadyError, match="sorter position is unknown"):
        require_supported(SortIntent(3), unhomed)


@pytest.mark.parametrize(
    ("axis", "firmware", "missing"),
    [
        (HomeAxis.FEEDER, FirmwareProfile(2, 102, 8, 2, True, False, True), "feed_home"),
        (HomeAxis.SORTER, FirmwareProfile(2, 102, 8, 2, True, True, False), "sort_home"),
        (HomeAxis.BOTH, FirmwareProfile(2, 102, 8, 2, True, False, False), "feed_home"),
    ],
)
def test_homing_an_axis_the_controller_does_not_advertise_is_unsupported(
    axis: HomeAxis,
    firmware: FirmwareProfile,
    missing: str,
) -> None:
    with pytest.raises(UnsupportedOperationError, match=missing) as raised:
        require_supported(HomeIntent(axis), _view(firmware=firmware))

    assert raised.value.code == "UNSUPPORTED"


def test_homing_needs_no_prior_homing() -> None:
    unhomed = _view(readiness=MachineReadiness(False, False, 0, "recovering", "idle"))

    require_supported(HomeIntent(HomeAxis.BOTH), unhomed)


def test_a_query_is_not_a_state_changing_operation() -> None:
    with pytest.raises(ValidationError, match="state-changing"):
        require_supported(QueryIntent(QueryKind.STATUS), _view())

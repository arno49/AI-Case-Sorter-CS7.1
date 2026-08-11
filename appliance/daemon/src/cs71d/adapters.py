"""Typed operation adapters between allow-listed requests and worker intents.

No caller can reach an arbitrary protocol command through this module. The
action selects the intent, the body must contain exactly that intent's own
field, and every value is validated against what the controller *advertised*
rather than against what the daemon hopes is true.

Validation happens in two stages, because the two answers mean different
things to a caller:

* :func:`intent_for` checks shape and vocabulary and rejects with
  ``VALIDATION_FAILED``. It needs no machine view.
* :func:`require_supported` checks the advertised capability, the firmware
  gate and observed readiness, and rejects with ``UNSUPPORTED`` or
  ``NOT_READY``. It runs against the frozen admission view, before anything
  is enqueued, so a rejected command performs no serial I/O.
"""

from __future__ import annotations

from .machine import MachineSnapshot
from .operations import (
    NotReadyError,
    OperationAction,
    RequestBody,
    UnsupportedOperationError,
    ValidationError,
)
from .serial_worker import HomeAxis, HomeIntent, SortIntent, WorkerIntent

FEED_LIFECYCLE_GATE = "NOT_EXECUTED"
"""The v2 feed lifecycle gate (V2-09 and its hardware evidence).

While this is ``NOT_EXECUTED`` the daemon refuses feed operations outright.
No firmware build advertises a v2 feed lifecycle yet, and a simulator run
cannot close a physical gate, so feed stays unavailable rather than being
attempted against unqualified firmware.
"""

_HOME_AXIS_CAPABILITY = {
    HomeAxis.FEEDER: ("feed_home",),
    HomeAxis.SORTER: ("sort_home",),
    HomeAxis.BOTH: ("feed_home", "sort_home"),
}


def intent_for(action: OperationAction, body: RequestBody) -> WorkerIntent:
    """Translate an allow-listed request body into a closed worker intent."""
    if action is OperationAction.STOP:
        raise ValidationError("a priority stop is admitted through OperationDomain.stop")
    if action is OperationAction.HOME:
        return _home_intent(body)
    if action is OperationAction.FEED:
        return _feed_intent(body)
    return _sort_intent(body)


def require_supported(intent: WorkerIntent, view: MachineSnapshot) -> None:
    """Refuse an intent the controller did not advertise or is not ready for."""
    firmware = view.firmware
    readiness = view.readiness
    if firmware is None or readiness is None:
        raise NotReadyError("controller capabilities have not been observed yet")

    if isinstance(intent, HomeIntent):
        missing = [
            name for name in _HOME_AXIS_CAPABILITY[intent.axis] if not getattr(firmware, name)
        ]
        if missing:
            raise UnsupportedOperationError(
                f"controller does not advertise {' and '.join(missing)}"
            )
        return

    if isinstance(intent, SortIntent):
        if intent.slot > firmware.slot_max:
            raise ValidationError(
                f"slot {intent.slot} exceeds the advertised maximum {firmware.slot_max}"
            )
        if not readiness.sort_homed:
            # The sorter position is unknown until it has been homed, so the
            # daemon cannot say where a sort would move to.
            raise NotReadyError("the sorter position is unknown; home the sorter first")
        return

    raise ValidationError("intent is not a state-changing machine operation")


def _home_intent(body: RequestBody) -> HomeIntent:
    if set(body) != {"axis"}:
        raise ValidationError("home takes exactly an axis")
    axis = body["axis"]
    if not isinstance(axis, str):
        raise ValidationError("home axis must be a string")
    try:
        return HomeIntent(HomeAxis(axis))
    except ValueError as exc:
        raise ValidationError(f"unknown home axis {axis!r}") from exc


def _sort_intent(body: RequestBody) -> SortIntent:
    if set(body) != {"slot"}:
        raise ValidationError("sort takes exactly a slot")
    slot = body["slot"]
    if isinstance(slot, bool) or not isinstance(slot, int):
        raise ValidationError("sort slot must be an integer")
    try:
        return SortIntent(slot)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def _feed_intent(body: RequestBody) -> WorkerIntent:
    """Refuse feed while its firmware lifecycle gate is open.

    The shape is validated first so a malformed request is still a validation
    failure, and the gate is reported afterwards. This is a firmware/hardware
    gate, not a configuration switch: it cannot be closed from software.
    """
    if set(body) != {"slot"}:
        raise ValidationError("feed takes exactly a slot")
    slot = body["slot"]
    if isinstance(slot, bool) or not isinstance(slot, int):
        raise ValidationError("feed slot must be an integer")
    raise UnsupportedOperationError(
        "the v2 feed lifecycle is unavailable: its firmware gate is"
        f" {FEED_LIFECYCLE_GATE} and no build advertises it"
    )

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import pytest

from cs71d.operations import (
    Actor,
    IdempotencyRecord,
    OperationAction,
    OperationRecord,
    OperationState,
    ValidationError,
    can_transition,
    canonical_request,
    is_terminal,
    new_operation_id,
    request_fingerprint,
    require_idempotency_key,
    require_reason,
)

CREATED = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
DEADLINE = CREATED + timedelta(seconds=30)
OPERATOR = Actor(user_id="opaque-bff-attribution", role="operator")


def _record(
    *,
    operation_id: str | None = None,
    state: OperationState = OperationState.QUEUED,
    created_at: datetime = CREATED,
    deadline_at: datetime = DEADLINE,
    trusted_terminal: bool = False,
    terminal_at: datetime | None = None,
) -> OperationRecord:
    return OperationRecord(
        operation_id=new_operation_id() if operation_id is None else operation_id,
        action=OperationAction.SORT,
        fingerprint=request_fingerprint(OperationAction.SORT, {"slot": 3}, OPERATOR),
        state=state,
        generation=7,
        created_at=created_at,
        deadline_at=deadline_at,
        actor=OPERATOR,
        trusted_terminal=trusted_terminal,
        terminal_at=terminal_at,
    )


def test_operation_ids_are_distinct_uuids() -> None:
    first, second = new_operation_id(), new_operation_id()

    assert UUID(first).version == 4
    assert first != second


def test_equivalent_requests_share_a_fingerprint_regardless_of_field_order() -> None:
    left = request_fingerprint(OperationAction.SORT, {"slot": 3, "retry": False}, OPERATOR)
    right = request_fingerprint(OperationAction.SORT, {"retry": False, "slot": 3}, OPERATOR)

    assert left == right


@pytest.mark.parametrize(
    ("action", "body", "actor"),
    [
        (OperationAction.HOME, {"slot": 3}, OPERATOR),
        (OperationAction.SORT, {"slot": 4}, OPERATOR),
        (OperationAction.SORT, {"slot": 3}, Actor(user_id="other", role="operator")),
        (OperationAction.SORT, {"slot": 3}, Actor(user_id=OPERATOR.user_id, role="maintainer")),
    ],
)
def test_any_differing_component_changes_the_fingerprint(
    action: OperationAction,
    body: dict[str, Any],
    actor: Actor,
) -> None:
    baseline = request_fingerprint(OperationAction.SORT, {"slot": 3}, OPERATOR)

    assert request_fingerprint(action, body, actor) != baseline


def test_boolean_and_integer_bodies_are_not_conflated() -> None:
    truthy = request_fingerprint(OperationAction.SORT, {"slot": True}, OPERATOR)
    numeric = request_fingerprint(OperationAction.SORT, {"slot": 1}, OPERATOR)

    assert truthy != numeric


def test_canonical_request_is_compact_sorted_json() -> None:
    encoded = canonical_request(OperationAction.SORT, {"slot": 3}, OPERATOR)

    assert encoded == (
        '{"action":"sort",'
        '"actor":{"role":"operator","user_id":"opaque-bff-attribution"},'
        '"body":{"slot":3}}'
    )


@pytest.mark.parametrize(
    "body",
    [
        {"Slot": 3},
        {"slot": 3.5},
        {"slot": [1, 2]},
        {"slot": "x" * 129},
        {f"field_{index}": index for index in range(17)},
    ],
)
def test_uncanonicalizable_bodies_are_rejected(body: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        canonical_request(OperationAction.SORT, body, OPERATOR)


@pytest.mark.parametrize("value", ["", " operator", "op erator", "é", "x" * 65])
def test_actor_attribution_must_be_a_restricted_token(value: str) -> None:
    with pytest.raises(ValidationError):
        Actor(user_id=value, role="operator")


@pytest.mark.parametrize("key", ["", "with space", "x" * 201, "tab\tkey"])
def test_idempotency_keys_must_be_bounded_printable_ascii(key: str) -> None:
    with pytest.raises(ValidationError):
        require_idempotency_key(key)


def test_reason_is_required_and_bounded() -> None:
    assert require_reason("admitted by operator") == "admitted by operator"
    with pytest.raises(ValidationError):
        require_reason("   ")
    with pytest.raises(ValidationError):
        require_reason("x" * 513)


def test_succeeded_is_reachable_only_from_running() -> None:
    reaching_success = [
        state for state in OperationState if can_transition(state, OperationState.SUCCEEDED)
    ]

    assert reaching_success == [OperationState.RUNNING]


def test_terminal_states_are_absorbing() -> None:
    for state in OperationState:
        if not is_terminal(state):
            continue
        assert not any(can_transition(state, target) for target in OperationState)


def test_queued_cannot_skip_admission_but_can_still_fail_closed() -> None:
    assert not can_transition(OperationState.QUEUED, OperationState.RUNNING)
    assert can_transition(OperationState.QUEUED, OperationState.ACCEPTED)
    assert can_transition(OperationState.QUEUED, OperationState.UNCERTAIN)


def test_record_requires_a_uuid_identity() -> None:
    with pytest.raises(ValidationError):
        _record(operation_id="not-a-uuid")


def test_record_requires_a_finite_deadline_after_creation() -> None:
    with pytest.raises(ValidationError):
        _record(deadline_at=CREATED)


def test_record_rejects_naive_timestamps_and_normalizes_offsets() -> None:
    with pytest.raises(ValidationError):
        _record(created_at=CREATED.replace(tzinfo=None))

    moscow = timezone(timedelta(hours=3))
    record = _record(created_at=CREATED.astimezone(moscow))

    assert record.created_at.tzinfo is UTC
    assert record.created_at == CREATED


def test_success_and_trusted_terminal_imply_each_other() -> None:
    with pytest.raises(ValidationError):
        _record(state=OperationState.SUCCEEDED, trusted_terminal=False)
    with pytest.raises(ValidationError):
        _record(state=OperationState.FAILED, trusted_terminal=True)

    record = _record(
        state=OperationState.SUCCEEDED,
        trusted_terminal=True,
        terminal_at=DEADLINE,
    )

    assert record.is_terminal


def test_expiry_is_evaluated_against_the_recorded_deadline() -> None:
    record = _record()

    assert not record.expired_at(DEADLINE - timedelta(milliseconds=1))
    assert record.expired_at(DEADLINE)


def test_idempotency_record_window_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        IdempotencyRecord(
            key="key-1",
            fingerprint="f" * 64,
            operation_id=new_operation_id(),
            created_at=CREATED,
            expires_at=CREATED,
        )

    record = IdempotencyRecord(
        key="key-1",
        fingerprint="f" * 64,
        operation_id=new_operation_id(),
        created_at=CREATED,
        expires_at=DEADLINE,
    )

    assert record.live_at(CREATED)
    assert not record.live_at(DEADLINE)

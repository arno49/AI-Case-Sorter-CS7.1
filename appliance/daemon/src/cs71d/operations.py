"""Durable operation identity, lifecycle and canonical request fingerprint.

This module is pure vocabulary: it performs no I/O and knows nothing about
SQLite or the serial worker. :mod:`cs71d.journal` persists these records and the
domain layer decides when a transition happens.

Four identifier spaces stay separate here, as required by the architecture:

* ``operation_id`` is a daemon-owned UUID and the durable audit identity.
* the snapshot ``generation`` is the daemon-owned machine-view version.
* the protocol ``request_id`` is session-scoped firmware correlation. It wraps,
  it is not unique across sessions, and it is recorded as diagnostic metadata
  only -- never as a key.
* daemon ``event_id`` belongs to the event ring and does not appear here.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import ClassVar
from uuid import UUID, uuid4


class DomainError(RuntimeError):
    """Base class for a rejected or impossible domain interaction.

    ``code`` is the machine-readable error code from
    ``docs/architecture/api-and-events.md``. It is part of the daemon's
    contract, so subclasses set it deliberately rather than incidentally.

    ``operation_id`` is present when the rejection already produced a durable
    operation, so a caller can look up what was recorded on its behalf.
    """

    code: ClassVar[str] = "INTERNAL_ERROR"

    def __init__(self, message: str, *, operation_id: str | None = None) -> None:
        super().__init__(message)
        self.operation_id = operation_id


class ValidationError(DomainError):
    """Caller-supplied identity, body or timing violates daemon policy."""

    code: ClassVar[str] = "VALIDATION_FAILED"


class InvalidTransitionError(DomainError):
    """A lifecycle transition the operation model does not permit.

    This is an internal defect rather than a caller error: the domain must
    never ask the journal to record an outcome the model forbids, such as
    success without a trusted firmware terminal.
    """


class OperationAction(StrEnum):
    """The state-changing machine intents that own a durable operation."""

    HOME = "home"
    SORT = "sort"


class OperationState(StrEnum):
    QUEUED = "queued"
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNCERTAIN = "uncertain"


TERMINAL_STATES = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.UNCERTAIN,
    }
)

_UNRESOLVED = frozenset({OperationState.FAILED, OperationState.CANCELLED, OperationState.UNCERTAIN})

# ``SUCCEEDED`` is reachable only from ``RUNNING``: a command that was never
# transmitted cannot have produced a trusted firmware terminal. Every other
# non-terminal state may still fail, be cancelled, or become uncertain.
_ALLOWED_TRANSITIONS: Mapping[OperationState, frozenset[OperationState]] = {
    OperationState.QUEUED: frozenset({OperationState.ACCEPTED}) | _UNRESOLVED,
    OperationState.ACCEPTED: frozenset({OperationState.RUNNING}) | _UNRESOLVED,
    OperationState.RUNNING: frozenset({OperationState.SUCCEEDED}) | _UNRESOLVED,
    OperationState.SUCCEEDED: frozenset(),
    OperationState.FAILED: frozenset(),
    OperationState.CANCELLED: frozenset(),
    OperationState.UNCERTAIN: frozenset(),
}

MAX_ACTOR_FIELD_LENGTH = 64
MAX_IDEMPOTENCY_KEY_LENGTH = 200
MAX_BODY_FIELDS = 16
MAX_BODY_STRING_LENGTH = 128
MAX_REASON_LENGTH = 512

_ACTOR_FIELD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@-]*\Z")
_BODY_FIELD = re.compile(r"[a-z][a-z0-9_]*\Z")
_IDEMPOTENCY_KEY = re.compile(r"[\x21-\x7e]+\Z")

type BodyValue = str | int | bool | None
type RequestBody = Mapping[str, BodyValue]


def is_terminal(state: OperationState) -> bool:
    return state in TERMINAL_STATES


def can_transition(from_state: OperationState, to_state: OperationState) -> bool:
    return to_state in _ALLOWED_TRANSITIONS[from_state]


def new_operation_id() -> str:
    """Return a fresh durable operation identity."""
    return str(uuid4())


@dataclass(frozen=True, slots=True)
class Actor:
    """Restricted attribution metadata propagated by the BFF.

    The daemon validates only the *format* of these fields. It never treats a
    caller-supplied role as an authority; SvelteKit authorizes the browser
    identity, and this record exists so a durable operation can be attributed.
    """

    user_id: str
    role: str

    def __post_init__(self) -> None:
        _require_token(self.user_id, "actor user_id")
        _require_token(self.role, "actor role")


@dataclass(frozen=True, slots=True)
class OperationRecord:
    """The durable identity and current lifecycle position of one operation."""

    operation_id: str
    action: OperationAction
    fingerprint: str
    state: OperationState
    generation: int
    created_at: datetime
    deadline_at: datetime
    actor: Actor
    trusted_terminal: bool = False
    outcome: str | None = None
    terminal_at: datetime | None = None
    protocol_request_id: int | None = None

    def __post_init__(self) -> None:
        try:
            UUID(self.operation_id)
        except ValueError as exc:
            raise ValidationError(f"operation_id must be a UUID: {exc}") from exc
        if self.generation < 1:
            raise ValidationError("generation must be positive")
        created_at = _require_utc(self.created_at, "created_at")
        deadline_at = _require_utc(self.deadline_at, "deadline_at")
        if deadline_at <= created_at:
            raise ValidationError("deadline_at must be a finite deadline after created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "deadline_at", deadline_at)
        if self.terminal_at is not None:
            object.__setattr__(self, "terminal_at", _require_utc(self.terminal_at, "terminal_at"))
        if self.trusted_terminal and self.state is not OperationState.SUCCEEDED:
            raise ValidationError("only a succeeded operation records a trusted terminal")
        if self.state is OperationState.SUCCEEDED and not self.trusted_terminal:
            raise ValidationError("a succeeded operation requires a trusted firmware terminal")

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self.state)

    def expired_at(self, moment: datetime) -> bool:
        """Report whether the finite deadline has passed at ``moment``."""
        return _require_utc(moment, "moment") >= self.deadline_at


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    """One append-only lifecycle audit entry."""

    transition_id: int
    operation_id: str
    from_state: OperationState | None
    to_state: OperationState
    generation: int
    occurred_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """A deduplication-window entry binding one key to one canonical request."""

    key: str
    fingerprint: str
    operation_id: str
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        require_idempotency_key(self.key)
        created_at = _require_utc(self.created_at, "created_at")
        expires_at = _require_utc(self.expires_at, "expires_at")
        if expires_at <= created_at:
            raise ValidationError("expires_at must be after created_at")
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)

    def live_at(self, moment: datetime) -> bool:
        return _require_utc(moment, "moment") < self.expires_at


def require_idempotency_key(key: str) -> str:
    """Validate an opaque caller-supplied idempotency key."""
    if not isinstance(key, str) or not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ValidationError("idempotency key must be printable ASCII without spaces")
    if len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ValidationError(
            f"idempotency key must be at most {MAX_IDEMPOTENCY_KEY_LENGTH} characters"
        )
    return key


def canonical_request(action: OperationAction, body: RequestBody, actor: Actor) -> str:
    """Return the canonical encoding two requests must share to be equivalent.

    The encoding covers the action, the validated request body and the actor
    attribution. Attribution is included deliberately: reusing one idempotency
    key across different actors is a differing request, and the fail-closed
    answer to that is a conflict rather than a silently shared operation.
    """
    if not isinstance(action, OperationAction):
        raise ValidationError("action must be a known operation action")
    if not isinstance(actor, Actor):
        raise ValidationError("actor attribution is required")
    if len(body) > MAX_BODY_FIELDS:
        raise ValidationError(f"request body must have at most {MAX_BODY_FIELDS} fields")

    encoded: dict[str, BodyValue] = {}
    for name, value in body.items():
        if not isinstance(name, str) or not _BODY_FIELD.fullmatch(name):
            raise ValidationError(f"request body field {name!r} is not lower_snake_case")
        encoded[name] = _require_body_value(name, value)

    return json.dumps(
        {
            "action": action.value,
            "actor": {"user_id": actor.user_id, "role": actor.role},
            "body": encoded,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def request_fingerprint(action: OperationAction, body: RequestBody, actor: Actor) -> str:
    """Return the stable fingerprint of a canonical request."""
    return sha256(canonical_request(action, body, actor).encode("utf-8")).hexdigest()


def require_reason(reason: str) -> str:
    """Validate a short human-readable audit reason."""
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("a transition reason is required")
    if len(reason) > MAX_REASON_LENGTH:
        raise ValidationError(f"reason must be at most {MAX_REASON_LENGTH} characters")
    return reason


def _require_body_value(name: str, value: BodyValue) -> BodyValue:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if not -(2**31) <= value < 2**31:
            raise ValidationError(f"request body field {name!r} is out of range")
        return value
    if isinstance(value, str):
        if len(value) > MAX_BODY_STRING_LENGTH:
            raise ValidationError(f"request body field {name!r} is too long")
        return value
    raise ValidationError(f"request body field {name!r} must be a JSON scalar")


def _require_token(value: str, name: str) -> str:
    if not isinstance(value, str) or not _ACTOR_FIELD.fullmatch(value):
        raise ValidationError(f"{name} must be a restricted attribution token")
    if len(value) > MAX_ACTOR_FIELD_LENGTH:
        raise ValidationError(f"{name} must be at most {MAX_ACTOR_FIELD_LENGTH} characters")
    return value


def _require_utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValidationError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(UTC)

"""Daemon policy values, versioned and applied through a durable operation.

These are the daemon's own settings, not machine settings: nothing here reaches
the controller, and applying a change moves no motor. They are still admitted,
validated and journaled like any other operation, because an appliance that
cannot say when a retention or deadline policy changed cannot explain its own
behaviour afterwards.

Bounds match the executable contract. The daemon re-validates them rather than
trusting the caller to have read the same document.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .operations import ValidationError

type ConfigurationChanges = Mapping[str, int]

_BOUNDS: Mapping[str, tuple[int, int]] = {
    "heartbeat_interval_ms": (1_000, 60_000),
    "event_retention_count": (100, 100_000),
    "operation_retention_days": (1, 365),
    "max_deadline_ms": (1_000, 120_000),
}


@dataclass(frozen=True, slots=True)
class ConfigurationValues:
    """The applied daemon policy at one generation."""

    heartbeat_interval_ms: int = 15_000
    event_retention_count: int = 5_000
    operation_retention_days: int = 30
    max_deadline_ms: int = 120_000

    def __post_init__(self) -> None:
        for name, (lowest, highest) in _BOUNDS.items():
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError(f"{name} must be an integer")
            if not lowest <= value <= highest:
                raise ValidationError(f"{name} must be between {lowest} and {highest}")

    def with_changes(self, changes: ConfigurationChanges) -> ConfigurationValues:
        """Return the values that applying ``changes`` would produce."""
        if not changes:
            raise ValidationError("a configuration change must change something")
        unknown = sorted(set(changes) - set(_BOUNDS))
        if unknown:
            raise ValidationError(f"unknown configuration field(s): {unknown}")
        return replace(self, **dict(changes))

    def as_mapping(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in _BOUNDS}

    @classmethod
    def from_mapping(cls, values: Mapping[str, int]) -> ConfigurationValues:
        unknown = sorted(set(values) - set(_BOUNDS))
        if unknown:
            raise ValidationError(f"unknown configuration field(s): {unknown}")
        return cls(**dict(values))

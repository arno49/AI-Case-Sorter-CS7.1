from __future__ import annotations

import inspect

import pytest

from cs71vision.primer import requires_operator_confirmation


@pytest.mark.parametrize(
    ("primer_present", "expected"),
    [
        (True, True),
        (None, True),
        (False, False),
    ],
)
def test_requires_operator_confirmation_is_true_unless_confidently_clear(
    primer_present: bool | None, expected: bool
) -> None:
    assert requires_operator_confirmation(primer_present) is expected


def test_requires_operator_confirmation_takes_no_configuration_parameter() -> None:
    """PI-VISION-010: no argument exists that could ever be passed to bypass this."""
    signature = inspect.signature(requires_operator_confirmation)
    assert list(signature.parameters) == ["primer_present"]

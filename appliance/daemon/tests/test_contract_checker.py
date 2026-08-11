"""The conformance checker's own tests, so a passing API test means something."""

from __future__ import annotations

from typing import Any

import pytest
from contract import SUPPORTED_KEYWORDS, ConformanceError, assert_conforms, load_contract

LIVENESS: dict[str, Any] = {
    "api_version": "v1",
    "live": True,
    "observed_at": "2026-08-11T12:00:00Z",
}


def test_the_contract_document_is_the_repository_contract() -> None:
    document = load_contract()

    assert document["openapi"].startswith("3.1")
    assert document["x-transport"] == "unix-domain-socket"


def test_a_conforming_payload_passes() -> None:
    assert_conforms(LIVENESS, "Liveness")


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ({"api_version": "v1", "live": True}, "missing required"),
        ({**LIVENESS, "extra": 1}, "unexpected field"),
        ({**LIVENESS, "api_version": "v2"}, "is not one of"),
        ({**LIVENESS, "live": False}, "must be True"),
        ({**LIVENESS, "observed_at": "yesterday"}, "not RFC 3339"),
        ({**LIVENESS, "observed_at": "2026-08-11T12:00:00"}, "no UTC offset"),
    ],
)
def test_a_non_conforming_payload_is_rejected(payload: dict[str, Any], problem: str) -> None:
    with pytest.raises(ConformanceError, match=problem):
        assert_conforms(payload, "Liveness")


def test_bounds_and_formats_are_actually_checked() -> None:
    with pytest.raises(ConformanceError, match="is below"):
        assert_conforms(-1, "Generation")
    with pytest.raises(ConformanceError, match="not a UUID"):
        assert_conforms(
            {
                "api_version": "v1",
                "operation_id": "not-a-uuid",
                "state": "QUEUED",
                "generation": 1,
                "accepted_at": "2026-08-11T12:00:00Z",
                "status_url": "/v1/operations/00000000-0000-4000-8000-000000000000",
            },
            "OperationAccepted",
        )
    with pytest.raises(ConformanceError, match="does not match"):
        assert_conforms(
            {
                "api_version": "v1",
                "operation_id": "00000000-0000-4000-8000-000000000000",
                "state": "QUEUED",
                "generation": 1,
                "accepted_at": "2026-08-11T12:00:00Z",
                "status_url": "/v1/nope",
            },
            "OperationAccepted",
        )


def test_the_checker_covers_every_keyword_the_contract_uses() -> None:
    """An unsupported keyword fails closed, so this proves nothing is skipped."""
    document = load_contract()
    seen: set[str] = set()

    def walk(schema: Any) -> None:
        if not isinstance(schema, dict):
            return
        seen.update(k for k in schema if not k.startswith("x-"))
        for child in schema.get("properties", {}).values():
            walk(child)
        for keyword in ("items", "additionalProperties"):
            walk(schema.get(keyword))
        for keyword in ("if", "then", "else"):
            walk(schema.get(keyword))
        for keyword in ("oneOf", "allOf"):
            for member in schema.get(keyword, []):
                walk(member)

    for schema in document["components"]["schemas"].values():
        walk(schema)

    assert seen - SUPPORTED_KEYWORDS == set()

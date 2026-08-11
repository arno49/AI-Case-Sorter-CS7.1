"""A targeted conformance checker for the frozen `cs71d` OpenAPI contract.

This is deliberately not a general JSON Schema implementation. It supports
exactly the keywords the contract uses, and it *fails closed*: an unsupported
keyword raises rather than being ignored, so the checker can never silently
approve a payload it did not actually validate. Its own negative tests live in
``test_contract_checker.py``.

The daemon workspace has no JSON Schema dependency, and adding one to validate
a document this repository already owns would buy less than it costs.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "contracts" / "cs71d-v1.openapi.json"

SUPPORTED_KEYWORDS = {
    "$ref",
    "allOf",
    "else",
    "additionalProperties",
    "const",
    "description",
    "enum",
    "format",
    "if",
    "items",
    "maxItems",
    "maxLength",
    "maximum",
    "minProperties",
    "minLength",
    "minimum",
    "oneOf",
    "pattern",
    "properties",
    "required",
    "then",
    "type",
}


class ConformanceError(AssertionError):
    """A payload does not conform to the frozen contract schema."""


def load_contract() -> dict[str, Any]:
    document: object = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if not isinstance(document, dict):  # pragma: no cover - the contract is a document
        raise ConformanceError("the contract document is not a JSON object")
    return {str(name): value for name, value in document.items()}


_CONTRACT = load_contract()
_SCHEMAS: dict[str, Any] = _CONTRACT["components"]["schemas"]


def assert_conforms(payload: Any, schema_name: str) -> None:
    """Raise unless ``payload`` conforms to the named contract schema."""
    _check(payload, _SCHEMAS[schema_name], schema_name)


def _resolve(schema: dict[str, Any]) -> dict[str, Any]:
    reference = schema.get("$ref")
    if reference is None:
        return schema
    name = reference.rsplit("/", 1)[-1]
    resolved: dict[str, Any] = _SCHEMAS[name]
    return resolved


def _check(value: Any, schema: dict[str, Any], path: str) -> None:
    schema = _resolve(schema)
    # Vendor extensions are documentation; everything else must be understood.
    unsupported = {
        keyword for keyword in set(schema) - SUPPORTED_KEYWORDS if not keyword.startswith("x-")
    }
    if unsupported:
        raise ConformanceError(f"{path}: checker does not support {sorted(unsupported)}")

    for member in schema.get("allOf", []):
        _check(value, member, path)

    if "if" in schema:
        # The contract states its safety invariants conditionally: a terminal
        # operation must carry an outcome, and only a trusted terminal may be
        # SUCCEEDED. Skipping these would hollow out the whole check.
        branch = "then" if _conforms(value, schema["if"], path) else "else"
        if branch in schema:
            _check(value, schema[branch], path)

    if "oneOf" in schema:
        for option in schema["oneOf"]:
            try:
                _check(value, option, path)
            except ConformanceError:
                continue
            return
        raise ConformanceError(f"{path}: matches none of the permitted schemas")

    if "type" in schema:
        _check_type(value, schema["type"], path)
    if "const" in schema and value != schema["const"]:
        raise ConformanceError(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ConformanceError(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        _check_string(value, schema, path)
    if isinstance(value, int) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ConformanceError(f"{path}: {value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ConformanceError(f"{path}: {value} is above {schema['maximum']}")
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ConformanceError(f"{path}: has more than {schema['maxItems']} items")
        for index, item in enumerate(value):
            _check(item, schema.get("items", {}), f"{path}[{index}]")
    if isinstance(value, dict):
        _check_object(value, schema, path)


def _conforms(value: Any, schema: dict[str, Any], path: str) -> bool:
    try:
        _check(value, schema, path)
    except ConformanceError:
        return False
    return True


def _check_type(value: Any, expected: str | list[str], path: str) -> None:
    names = [expected] if isinstance(expected, str) else expected
    if not any(_is_type(value, name) for name in names):
        raise ConformanceError(f"{path}: {type(value).__name__} is not {names}")


def _is_type(value: Any, name: str) -> bool:
    if name == "null":
        return value is None
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if name == "string":
        return isinstance(value, str)
    if name == "array":
        return isinstance(value, list)
    if name == "object":
        return isinstance(value, dict)
    raise ConformanceError(f"checker does not support type {name!r}")


def _check_string(value: str, schema: dict[str, Any], path: str) -> None:
    if "minLength" in schema and len(value) < schema["minLength"]:
        raise ConformanceError(f"{path}: shorter than {schema['minLength']}")
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        raise ConformanceError(f"{path}: longer than {schema['maxLength']}")
    pattern = schema.get("pattern")
    if pattern is not None and not re.search(pattern, value):
        raise ConformanceError(f"{path}: {value!r} does not match {pattern}")
    fmt = schema.get("format")
    if fmt == "uuid":
        try:
            UUID(value)
        except ValueError as exc:
            raise ConformanceError(f"{path}: {value!r} is not a UUID") from exc
    elif fmt == "date-time":
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ConformanceError(f"{path}: {value!r} is not RFC 3339") from exc
        if parsed.tzinfo is None:
            raise ConformanceError(f"{path}: {value!r} has no UTC offset")


def _check_object(value: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    properties: dict[str, Any] = schema.get("properties", {})
    for name in schema.get("required", []):
        if name not in value:
            raise ConformanceError(f"{path}: missing required field {name!r}")
    if "minProperties" in schema and len(value) < schema["minProperties"]:
        raise ConformanceError(f"{path}: needs at least {schema['minProperties']} field(s)")
    if schema.get("additionalProperties") is False:
        unexpected = sorted(set(value) - set(properties))
        if unexpected:
            raise ConformanceError(f"{path}: unexpected field(s) {unexpected}")
    for name, item in value.items():
        if name in properties:
            _check(item, properties[name], f"{path}.{name}")

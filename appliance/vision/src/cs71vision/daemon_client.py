"""A minimal HTTP client for `cs71d`'s private Unix-socket API.

Reading is unrestricted: enough to notice a newly `SUCCEEDED` sort operation
and read the slot it reached from `terminal_fields` (the only place that
value is ever exposed - `cs71d` records no other durable, externally
readable trace of "which slot"), and to read the current snapshot
generation. Writing is exactly one thing, and only with its own distinct
credential: `submit_sort` is the sole place this codebase may ever present
the `machine` service credential PI-VISION-007 added to `cs71d`'s contract
(PI-VISION-008) - every other request here still presents the ordinary
shared credential, the same three-file pattern `cs71d`/`cs71-web` already
use.
"""

from __future__ import annotations

import http.client
import json
import socket
from dataclasses import dataclass
from typing import Any


class DaemonClientError(RuntimeError):
    """`cs71d` could not be reached, or returned an unusable response."""


@dataclass(frozen=True, slots=True)
class SortSuccess:
    """One `SUCCEEDED` sort operation, as far as this client needs it."""

    operation_id: str
    slot: int
    created_at: str


class _UnixHTTPConnection(http.client.HTTPConnection):
    """`http.client.HTTPConnection` over `AF_UNIX` instead of TCP.

    The host name given to the base class is never resolved or dialed; only
    `connect()`'s override matters. Mirrors the same pattern
    `appliance/daemon/tests/test_api.py`'s own Unix client test double uses.
    """

    def __init__(self, socket_path: str, *, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(self._socket_path)
        self.sock = connection


class DaemonClient:
    """Read access to `cs71d`'s operation history, plus the one write this
    service is ever permitted: an autonomous sort, submitted with its own
    distinct credential.
    """

    def __init__(
        self,
        socket_path: str,
        service_token: str,
        *,
        machine_service_token: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        if not service_token:
            raise ValueError("a service token is required")
        if machine_service_token == "":
            raise ValueError("machine_service_token must not be empty when provided")
        self._socket_path = socket_path
        self._token = service_token
        self._machine_token = machine_service_token
        self._timeout = timeout

    def recent_sort_successes(self, *, limit: int = 25) -> tuple[SortSuccess, ...]:
        """Return the newest `SUCCEEDED` sort operations that carry a slot.

        An entry missing or malformed `terminal_fields.slot` is silently
        skipped rather than raising: this is read evidence from the
        contract's optional field, not something this client validates on
        `cs71d`'s behalf.
        """
        body = self._get(f"/v1/operations?type=SORT&state=SUCCEEDED&limit={limit}")
        items = body.get("items")
        if not isinstance(items, list):
            raise DaemonClientError("malformed operation page: no items")
        successes = []
        for item in items:
            success = _sort_success_from(item)
            if success is not None:
                successes.append(success)
        return tuple(successes)

    def current_generation(self) -> int:
        """The daemon's current snapshot generation.

        PI-VISION-008 reads a fresh one immediately before every autonomous
        sort attempt - the same `If-Match-Generation` contract any other
        commanding caller follows; there is no exemption for the `machine`
        role (`docs/architecture/api-and-events.md`).
        """
        body = self._get("/v1/snapshot")
        generation = body.get("generation")
        if not isinstance(generation, int) or isinstance(generation, bool):
            raise DaemonClientError("malformed snapshot: no usable generation")
        return generation

    def submit_sort(
        self,
        *,
        slot: int,
        generation: int,
        idempotency_key: str,
        deadline_ms: int = 15_000,
        user_id: str = "cs71-vision",
    ) -> str:
        """Submit exactly one autonomous sort, as the `machine` actor kind.

        Refuses outright when no machine credential is configured - this is
        the only place in this codebase that could ever present it, and it
        simply has nothing to present. There is no fallback to the ordinary
        shared credential: `cs71d` would refuse it anyway
        (`_commanding_actor`'s bidirectional identity/role check,
        PI-VISION-007), but this method never tries.
        """
        if self._machine_token is None:
            raise DaemonClientError(
                "no machine service credential configured; autonomous sort is unreachable"
            )
        request_body = json.dumps(
            {
                "api_version": "v1",
                "actor": {"user_id": user_id, "role": "machine"},
                "slot": slot,
            }
        ).encode("utf-8")
        response = self._post(
            "/v1/operations/sort",
            request_body,
            token=self._machine_token,
            headers={
                "Idempotency-Key": idempotency_key,
                "If-Match-Generation": str(generation),
                "X-Deadline-Ms": str(deadline_ms),
            },
        )
        operation_id = response.get("operation_id")
        if not isinstance(operation_id, str):
            raise DaemonClientError("malformed sort acceptance: no usable operation_id")
        return operation_id

    def _get(self, path: str) -> dict[str, Any]:
        _status, body = self._request("GET", path, token=self._token)
        return body

    def _post(
        self, path: str, request_body: bytes, *, token: str, headers: dict[str, str]
    ) -> dict[str, Any]:
        _status, body = self._request(
            "POST", path, token=token, request_body=request_body, headers=headers
        )
        return body

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        request_body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        connection = _UnixHTTPConnection(self._socket_path, timeout=self._timeout)
        all_headers = {"Authorization": f"Bearer {token}", **(headers or {})}
        try:
            connection.request(method, path, body=request_body, headers=all_headers)
            response = connection.getresponse()
            payload = response.read()
        except OSError as exc:
            raise DaemonClientError(f"cannot reach cs71d at {self._socket_path}: {exc}") from exc
        finally:
            connection.close()
        if response.status not in (200, 202):
            raise DaemonClientError(
                f"{method} {path} returned HTTP {response.status}:"
                f" {payload.decode(errors='replace')}"
            )
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DaemonClientError(f"{method} {path} returned unparseable JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise DaemonClientError(f"{method} {path} did not return a JSON object")
        return response.status, decoded


def _sort_success_from(item: object) -> SortSuccess | None:
    if not isinstance(item, dict):
        return None
    if item.get("type") != "SORT" or item.get("state") != "SUCCEEDED":
        return None
    fields = item.get("terminal_fields")
    if not isinstance(fields, dict):
        return None
    raw_slot = fields.get("slot")
    if not isinstance(raw_slot, str):
        return None
    try:
        slot = int(raw_slot)
    except ValueError:
        return None
    operation_id = item.get("operation_id")
    created_at = item.get("created_at")
    if not isinstance(operation_id, str) or not isinstance(created_at, str):
        return None
    return SortSuccess(operation_id=operation_id, slot=slot, created_at=created_at)

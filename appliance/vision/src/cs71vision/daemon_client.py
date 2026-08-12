"""A minimal, read-only HTTP client for `cs71d`'s private Unix-socket API.

Only what PI-VISION-002 needs: enough to notice a newly `SUCCEEDED` sort
operation and read the slot it reached from `terminal_fields` (the only
place that value is ever exposed - `cs71d` records no other durable,
externally readable trace of "which slot"). This client never writes: it
authenticates with its own service credential, the same three-file pattern
`cs71d`/`cs71-web` already use, but only ever issues `GET` requests.
Submitting commands is PI-VISION-007's new machine-actor kind, not this.
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
    """Read-only access to `cs71d`'s operation history."""

    def __init__(
        self,
        socket_path: str,
        service_token: str,
        *,
        timeout: float = 5.0,
    ) -> None:
        if not service_token:
            raise ValueError("a service token is required")
        self._socket_path = socket_path
        self._token = service_token
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

    def _get(self, path: str) -> dict[str, Any]:
        connection = _UnixHTTPConnection(self._socket_path, timeout=self._timeout)
        try:
            connection.request("GET", path, headers={"Authorization": f"Bearer {self._token}"})
            response = connection.getresponse()
            payload = response.read()
        except OSError as exc:
            raise DaemonClientError(f"cannot reach cs71d at {self._socket_path}: {exc}") from exc
        finally:
            connection.close()
        if response.status != 200:
            raise DaemonClientError(
                f"GET {path} returned HTTP {response.status}: {payload.decode(errors='replace')}"
            )
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise DaemonClientError(f"GET {path} returned unparseable JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise DaemonClientError(f"GET {path} did not return a JSON object")
        return decoded


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

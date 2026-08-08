"""Request-ID lifecycle and event sequence tracking."""

from __future__ import annotations

from .errors import RequestStateError
from .models import Event, Response


class RequestTracker:
    """Allocate IDs and reject uncorrelated or duplicate terminal responses."""

    def __init__(self) -> None:
        self.active: set[int] = set()
        self.terminal_ids: set[int] = set()
        self._next = 1

    def allocate(self) -> int:
        for _ in range(65535):
            candidate = self._next
            self._next = 1 if candidate == 65535 else candidate + 1
            if candidate not in self.active:
                self.active.add(candidate)
                self.terminal_ids.discard(candidate)
                return candidate
        raise RequestStateError("all request IDs are active")

    def reserve(self, request_id: int) -> None:
        if not 1 <= request_id <= 65535 or request_id in self.active:
            raise RequestStateError("request ID unavailable")
        self.active.add(request_id)
        self.terminal_ids.discard(request_id)

    def observe(self, response: Response) -> None:
        if response.request_id not in self.active:
            if response.terminal and response.request_id in self.terminal_ids:
                raise RequestStateError(f"duplicate terminal for ID {response.request_id}")
            raise RequestStateError(f"unexpected response for ID {response.request_id}")
        if response.terminal:
            self.active.remove(response.request_id)
            self.terminal_ids.add(response.request_id)

    def release(self, request_id: int) -> None:
        """Forget an ID when formatting failed before its request was sent."""
        self.active.discard(request_id)

    def clear(self) -> None:
        self.active.clear()
        self.terminal_ids.clear()
        self._next = 1


class EventTracker:
    """Track modular event order and expose a status-resynchronization requirement."""

    def __init__(self) -> None:
        self.last_sequence: int | None = None
        self.resync_required = False

    def observe(self, event: Event) -> bool:
        expected = 1 if self.last_sequence is None else (
            1 if self.last_sequence == 65535 else self.last_sequence + 1
        )
        contiguous = event.sequence == expected
        if not contiguous:
            self.resync_required = True
        self.last_sequence = event.sequence
        return contiguous

    def replace_status(self) -> None:
        self.resync_required = False

import pytest

from cs71_protocol import Event, EventKind, EventTracker, RequestStateError, RequestTracker, Response, ResponseKind


def test_id_allocator_wrap_skips_active_and_rejects_duplicate_terminal():
    tracker = RequestTracker()
    tracker._next = 65535
    assert tracker.allocate() == 65535
    assert tracker.allocate() == 1
    done = Response(65535, ResponseKind.DONE)
    tracker.observe(done)
    with pytest.raises(RequestStateError, match="duplicate"):
        tracker.observe(done)
    with pytest.raises(RequestStateError, match="unexpected"):
        tracker.observe(Response(33, ResponseKind.DONE))


def test_event_wrap_gap_and_status_resynchronization():
    tracker = EventTracker()
    assert not tracker.observe(Event(65535, EventKind.STATE, {"mode": "running"}))
    assert tracker.resync_required
    tracker.replace_status()
    assert not tracker.resync_required
    tracker.last_sequence = 65535
    assert tracker.observe(Event(1, EventKind.STATE, {"mode": "running"}))
    assert not tracker.observe(Event(3, EventKind.STATE, {"mode": "running"}))
    assert tracker.resync_required
    tracker.replace_status()
    assert not tracker.resync_required

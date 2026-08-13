from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cs71d.events import DaemonEvent, EventRing

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def _publish(ring: EventRing, count: int, *, first_generation: int = 1) -> list[DaemonEvent]:
    return [
        ring.publish(
            "snapshot.changed",
            generation=first_generation + index,
            occurred_at=NOW,
        )
        for index in range(count)
    ]


def test_every_event_carries_a_monotonic_cursor_and_its_generation() -> None:
    ring = EventRing()

    published = _publish(ring, 3, first_generation=7)

    assert [event.event_id for event in published] == [1, 2, 3]
    assert [event.generation for event in published] == [7, 8, 9]
    assert all(event.occurred_at is NOW for event in published)
    assert all(event.type == "snapshot.changed" for event in published)


def test_a_subscriber_receives_events_published_after_it_attached() -> None:
    ring = EventRing()
    _publish(ring, 2)

    with ring.subscribe() as subscriber:
        _publish(ring, 2, first_generation=100)
        drained = subscriber.drain(timeout=1.0)

    assert [event.event_id for event in drained] == [3, 4]
    assert ring.subscriber_count == 0


def test_a_retained_cursor_resumes_in_order() -> None:
    ring = EventRing()
    _publish(ring, 4)

    with ring.subscribe(after=2) as subscriber:
        drained = subscriber.drain(timeout=1.0)

    assert [event.event_id for event in drained] == [3, 4]
    assert not subscriber.overflowed


def test_a_cursor_the_ring_no_longer_retains_cannot_be_resumed() -> None:
    ring = EventRing(retention=3)
    _publish(ring, 6)

    with ring.subscribe(after=1) as subscriber:
        assert subscriber.overflowed
        # An overflowed subscriber yields nothing; the caller must reconcile.
        assert subscriber.drain(timeout=0.05) == []


def test_resuming_from_the_newest_cursor_is_not_an_overflow() -> None:
    ring = EventRing(retention=3)
    _publish(ring, 6)

    with ring.subscribe(after=6) as subscriber:
        assert not subscriber.overflowed
        assert subscriber.drain(timeout=0.05) == []


def test_a_subscriber_that_stops_reading_is_dropped_rather_than_blocking() -> None:
    ring = EventRing(subscriber_capacity=2)

    with ring.subscribe() as subscriber:
        _publish(ring, 10)

        assert subscriber.overflowed
        assert subscriber.drain(timeout=0.05) == []


def test_publishing_never_waits_for_a_stalled_subscriber() -> None:
    ring = EventRing(subscriber_capacity=1)
    finished = threading.Event()

    with ring.subscribe():

        def publish_many() -> None:
            _publish(ring, 200)
            finished.set()

        thread = threading.Thread(target=publish_many, name="publisher")
        thread.start()
        # The publisher completes without the subscriber ever draining.
        assert finished.wait(2.0)
        thread.join(2.0)

    assert len(ring.retained) == 200


def test_retention_is_bounded() -> None:
    ring = EventRing(retention=4)

    _publish(ring, 10)

    retained = ring.retained
    assert len(retained) == 4
    assert [event.event_id for event in retained] == [7, 8, 9, 10]


def test_a_closed_subscriber_stops_receiving_and_detaches() -> None:
    ring = EventRing()
    subscriber = ring.subscribe()
    subscriber.close()

    _publish(ring, 1)

    assert ring.subscriber_count == 0
    assert subscriber.drain(timeout=0.05) == []


@pytest.mark.parametrize(("retention", "capacity"), [(0, 1), (1, 0), (-1, -1)])
def test_a_ring_must_be_bounded_by_positive_limits(retention: int, capacity: int) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        EventRing(retention=retention, subscriber_capacity=capacity)


def test_a_cursor_from_a_previous_daemon_life_cannot_be_resumed() -> None:
    """A restarted daemon reissues ids, so an older cursor is not comparable."""
    ring = EventRing()
    _publish(ring, 2)

    with ring.subscribe(after=99) as subscriber:
        assert subscriber.overflowed
        assert subscriber.drain(timeout=0.05) == []


@given(
    retention=st.integers(min_value=1, max_value=20),
    published=st.integers(min_value=0, max_value=60),
)
def test_retained_never_exceeds_retention_however_many_events_are_published(
    retention: int, published: int
) -> None:
    """PI-SWQ-001 property test: `retained` is bounded by `retention`, for
    any number of publishes - not just the one worked example above.
    """
    ring = EventRing(retention=retention)

    _publish(ring, published)

    retained = ring.retained
    assert len(retained) == min(retention, published)
    if retained:
        # The window is always the newest events, contiguous, nothing skipped.
        assert [event.event_id for event in retained] == list(
            range(retained[0].event_id, retained[0].event_id + len(retained))
        )
        assert retained[-1].event_id == published


@given(
    retention=st.integers(min_value=1, max_value=15),
    published=st.integers(min_value=1, max_value=30),
    cursor=st.integers(min_value=0, max_value=45),
)
def test_a_resumed_cursor_is_either_overflowed_or_exactly_the_newer_events(
    retention: int, published: int, cursor: int
) -> None:
    """PI-SWQ-001 property test: for any retention/publish/cursor combination,
    a resumed subscriber never silently skips an event and never fabricates
    one - it is overflowed (cursor outside the retained window) or it is
    primed with exactly the retained events newer than the cursor, in order.
    """
    ring = EventRing(retention=retention)
    _publish(ring, published)
    oldest = published - min(retention, published) + 1 if published else 1

    with ring.subscribe(after=cursor) as subscriber:
        if cursor > published or cursor + 1 < oldest:
            assert subscriber.overflowed
            assert subscriber.drain(timeout=0.05) == []
        else:
            assert not subscriber.overflowed
            drained = subscriber.drain(timeout=0.05)
            assert [event.event_id for event in drained] == list(range(cursor + 1, published + 1))

"""Bounded no-hardware stress coverage for the V2-11 software preintegration."""

import pytest

from cs71_protocol import (
    Event,
    EventKind,
    EventTracker,
    FramingError,
    LineReader,
    ProtocolClient,
    RequestStateError,
    RequestTracker,
    Response,
    ResponseKind,
    SessionMode,
    ScriptedTransport,
    append_crc,
    crc16_ccitt_false,
)


def test_request_ids_wrap_across_all_values_without_reusing_active_ids():
    tracker = RequestTracker()
    allocated = {tracker.allocate() for _ in range(65535)}

    assert allocated == set(range(1, 65536))
    with pytest.raises(RequestStateError, match="all request IDs"):
        tracker.allocate()

    tracker.observe(Response(1, ResponseKind.DONE))
    tracker._next = 65535
    assert tracker.allocate() == 1

    tracker = RequestTracker()
    tracker.reserve(65535)
    tracker.reserve(1)
    tracker._next = 65535
    assert tracker.allocate() == 2


def test_event_sequence_full_wrap_gap_resynchronization_and_session_reset():
    tracker = EventTracker()
    event = lambda sequence: Event(sequence, EventKind.STATE, {"mode": "running"})

    assert tracker.observe(event(1))
    assert not tracker.resync_required
    first_gap = EventTracker()
    assert not first_gap.observe(event(2))
    assert first_gap.resync_required
    tracker.replace_status()
    assert not tracker.resync_required

    assert all(tracker.observe(event(sequence)) for sequence in range(2, 65536))
    assert tracker.observe(event(1))
    assert not tracker.resync_required

    assert not tracker.observe(event(3))
    assert tracker.resync_required
    tracker.replace_status()
    assert not tracker.resync_required

    reconnected = EventTracker()
    assert reconnected.last_sequence is None
    assert not reconnected.resync_required
    assert reconnected.observe(event(1))


def test_reader_recovers_after_each_rejected_v2_frame():
    valid = b"@1 done\n"
    stream = ScriptedTransport([
        b"x" * 65 + b"\n" + valid,
        b"@1 d\x01ne\n" + valid,
        b"@1 d\xffne\n" + valid,
    ])
    reader = LineReader(stream)

    for reason in ("exceeds 64", "non-printable", "non-printable"):
        with pytest.raises(FramingError, match=reason):
            reader.read_line(.02, v2=True)
        assert reader.read_line(.02, v2=True) == "@1 done"


def test_repeated_recovery_resets_volatile_host_session_state():
    recoveries: list[None] = []
    transport = ScriptedTransport(reset_incoming=["Ready\n", " ok\n"])
    client = ProtocolClient(transport, timeout=.02, on_recovery=lambda: recoveries.append(None))

    for request_id in range(1, 4):
        client.mode = SessionMode.V2
        client.crc_enabled = True
        client.requests.reserve(request_id)
        client.events.observe(Event(4, EventKind.STATE, {"mode": "running"}))
        client.status = object()  # type: ignore[assignment]
        transport.incoming.append(b"stopped\n")

        client.recover_to_v1()

        assert not client.crc_enabled
        assert not client.requests.active
        assert client.events.last_sequence is None
        assert not client.events.resync_required
        assert client.status is None

    assert transport.resets == 3
    assert len(recoveries) == 3


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (b"123456789", 0x29B1),
        (b"@1 done:crc=on", 0xCF68),
        (b"@2 crc:off", 0xD690),
        (b"@2 done:crc=off", 0x48C9),
        (b"!16 reject:1002:bad_crc", 0x452B),
    ],
)
def test_crc_matches_firmware_fixture_and_normative_vectors(frame, expected):
    assert crc16_ccitt_false(frame) == expected
    assert append_crc(frame.decode("ascii")) == f"{frame.decode('ascii')}*{expected:04X}"

import pytest

from cs71_protocol import (Event, EventKind, ParseError, Response, ResponseKind, V1ResponseKind,
                           classify_discovery, classify_v1_response, format_request, parse_v2_line)


def test_discovery_is_strict_and_old_firmware_ok_is_not_v2():
    assert classify_discovery("ok").value == "legacy"
    assert classify_discovery("protocol:2 available").value == "available"
    for unsafe in (" ok", "protocol:2 available ", "protocol:2 ready", "OK"):
        with pytest.raises(ParseError):
            classify_discovery(unsafe)
    assert classify_v1_response(" ok") is V1ResponseKind.PING
    assert classify_v1_response("ok") is V1ResponseKind.OK
    assert classify_v1_response('{"FeedMotorSpeed":90}') is V1ResponseKind.CONFIG
    assert classify_v1_response(" ok ").value == "unknown"


def test_response_event_unknown_fields_and_unframed_stop():
    result = parse_v2_line("@5 accepted:operation=sort future=v")
    assert isinstance(result, Response)
    assert result.kind is ResponseKind.ACCEPTED and result.fields["future"] == "v"
    assert parse_v2_line("@5 accepted").kind is ResponseKind.ACCEPTED
    assert parse_v2_line("@5 done").kind is ResponseKind.DONE
    event = parse_v2_line("!65535 fault:3001:feed_overtravel latched=1 unknown=y")
    assert isinstance(event, Event)
    assert event.kind is EventKind.FAULT and event.code == 3001 and event.fields["unknown"] == "y"
    assert parse_v2_line("stopped") == "stopped"
    assert parse_v2_line("stopped", crc_required=True) == "stopped"


def test_unknown_terminal_forms_and_malformed_values_fail_closed():
    for line in ("@1 complete", "@1 donez", "@1 done:bad", "@1 error:x:bad", "@1 data:x=1 x=2",
                 "@1 accepted:", "@1 progress:", "@1 data:", "@1 done:", "!1 state:",
                 "!1 mystery:x=1", "Ready"):
        with pytest.raises(ParseError):
            parse_v2_line(line)
    with pytest.raises(ParseError):
        format_request(0, "status")
    with pytest.raises(ParseError):
        format_request(1, "x" * 59, crc=True)


@pytest.mark.parametrize("command", ["status\n", "status\r", "status\x00", "status\tverbose",
                                     "status\x7f", "status\u00a0verbose"])
def test_request_format_rejects_non_printable_or_non_ascii_command_characters(command):
    with pytest.raises(ParseError):
        format_request(1, command)


def test_request_format_enforces_64_byte_payload_limit():
    assert format_request(65535, "x" * 57) == "@65535 " + "x" * 57
    with pytest.raises(ParseError, match="64"):
        format_request(65535, "x" * 58)

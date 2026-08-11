import pytest

from cs71_protocol import (ProtocolClient, ProtocolError, RecoveryError, RequestInterruptedError,
                           ScriptedTransport, SessionMode, append_crc)
from cs71_protocol import client as client_module


def activation_validation_responses() -> list[str]:
    return [
        "@1 data:protocol=2\n", "@1 done\n",
        "@2 data:protocol=2 max_line=64 crc=none\n",
        "@2 data:queue_depth=2 slot_max=102 slot_count=8\n",
        "@2 data:pwm=0 airdrop=1 feed_sensor=1\n",
        "@2 data:feed_home=1 sort_home=1\n", "@2 done\n",
        "@3 data:mode=running phase=idle feed_homed=1\n",
        "@3 data:sort_homed=1 motor_enabled=0 active_id=none\n",
        "@3 data:fault_code=0 queue_previous=0 queue_next=0\n",
        "@3 data:config_generation=0\n", "@3 done\n",
    ]


def test_old_and_new_discovery_activation_status_unknown_fields_and_lifecycle():
    old = ScriptedTransport(["ok\n"])
    assert ProtocolClient(old, timeout=.02).activate() is False
    assert old.writes == [b"protocol:2?\n"]

    incoming = [
        "protocol:2 available\r\n", "protocol:2 ready\n",
        "@1 data:protocol=2\n", "@1 done\n",
        "@2 data:protocol=2 max_line=64 crc=none\n",
        "@2 data:queue_depth=2 slot_max=102 slot_count=8\n",
        "@2 data:pwm=0 airdrop=1 feed_sensor=1\n",
        "@2 data:feed_home=1 sort_home=1\n", "@2 done\n",
        "@3 data:mode=running phase=idle feed_homed=1\n",
        "@3 data:sort_homed=1 motor_enabled=0 active_id=none\n",
        "@3 data:fault_code=0 queue_previous=0 queue_next=3\n",
        "@3 data:config_generation=4 future=x\n", "@3 done\n",
        "@4 accepted:operation=sort\n", "@4 progress:phase=sort_move\n", "@4 done:slot=3 extra=value\n",
    ]
    transport = ScriptedTransport(incoming)
    client = ProtocolClient(transport, timeout=.02)
    assert client.activate()
    status = client.status
    assert status is not None
    assert status.queue_next == 3 and status.extras == {"future": "x"}
    completion = client.request("sortto:3")
    assert completion.succeeded and completion.fields["extra"] == "value"
    assert [response.kind.value for response in completion.responses] == ["accepted", "progress", "done"]
    assert transport.writes == [
        b"protocol:2?\n", b"protocol:2\n", b"@1 protocolversion\n", b"@2 capabilities\n",
        b"@3 status\n", b"@4 sortto:3\n",
    ]


def test_crc_transition_boundaries_and_bad_crc_recover_to_v1():
    transport = ScriptedTransport(
        [
            "protocol:2 available\n", "protocol:2 ready\n", *activation_validation_responses(),
            append_crc("@4 done:crc=on") + "\n", append_crc("@5 done:crc=off") + "\n",
        ],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(transport, timeout=.02)
    assert client.activate()
    client.enable_crc()
    assert client.crc_enabled
    client.disable_crc()
    assert not client.crc_enabled
    assert transport.writes[-2] == b"@4 crc:on\n"
    assert transport.writes[-1] == append_crc("@5 crc:off").encode() + b"\n"

    bad = ScriptedTransport(
        [
            "protocol:2 available\n", "protocol:2 ready\n", *activation_validation_responses(),
            "@4 done:crc=on*0000\n",
        ],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(bad, timeout=.02)
    assert client.activate()
    with pytest.raises(RecoveryError):
        client.enable_crc()
    assert client.mode is SessionMode.V1 and bad.resets == 1


def test_crc_protects_protocol_v1_terminal_until_its_lf_boundary():
    transport = ScriptedTransport([
        "protocol:2 available\n", "protocol:2 ready\n",
        *activation_validation_responses(),
        append_crc("@4 done:crc=on") + "\n",
        append_crc("@5 done:protocol=1") + "\n",
    ])
    client = ProtocolClient(transport, timeout=.02)
    assert client.activate()
    client.enable_crc()
    client.leave_v2()
    assert transport.writes[-1] == append_crc("@5 protocol:1").encode() + b"\n"
    assert client.mode is SessionMode.V1 and not client.crc_enabled


def test_lost_activation_response_resets_and_verifies_v1():
    transport = ScriptedTransport(
        ["protocol:2 available\n"], reset_incoming=["Ready\r\n", " ok\r\n"]
    )
    client = ProtocolClient(transport, timeout=.01)
    with pytest.raises(RecoveryError):
        client.activate()
    assert client.mode is SessionMode.V1
    assert transport.resets == 1
    assert transport.writes == [b"protocol:2?\n", b"protocol:2\n", b"stop\n", b"ping\n"]


def test_activation_validation_failure_recovers_to_verified_v1():
    transport = ScriptedTransport(
        [
            "protocol:2 available\n", "protocol:2 ready\n",
            "@1 data:protocol=1\n", "@1 done\n", "stopped\n",
        ],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(transport, timeout=.02)

    with pytest.raises(RecoveryError):
        client.activate()

    assert client.mode is SessionMode.V1
    assert transport.resets == 1
    assert transport.writes == [
        b"protocol:2?\n", b"protocol:2\n", b"@1 protocolversion\n", b"stop\n", b"ping\n",
    ]


class StopRespondingTransport(ScriptedTransport):
    """Provide the out-of-band stop terminal only after the client sends stop."""

    def write(self, data: bytes) -> int:
        result = super().write(data)
        if data == b"stop\n":
            self.incoming.append(b"stopped\n")
        return result


class CancellingTransport(ScriptedTransport):
    """Emit normative cancellation evidence after the universal stop write."""

    def write(self, data: bytes) -> int:
        result = super().write(data)
        if data == b"stop\n":
            self.incoming.extend(
                [
                    b"@1 error:2004:cancelled by=0\n",
                    b"!1 state:mode=stopped phase=idle\n",
                    b"stopped\n",
                ]
            )
        return result


def test_request_interrupt_uses_trusted_stop_and_clears_correlation():
    transport = CancellingTransport()
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RequestInterruptedError, match="trusted out-of-band stop"):
        client.request("sortto:3", interrupt_requested=lambda: True)

    assert transport.writes == [b"@1 sortto:3\n", b"stop\n"]
    assert not client.requests.active
    assert client.mode is SessionMode.V2


def test_false_request_interrupt_does_not_change_normal_completion():
    transport = ScriptedTransport(["@1 done:slot=3\n"])
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    completion = client.request("sortto:3", interrupt_requested=lambda: False)

    assert completion.succeeded
    assert completion.terminal_fields == {"slot": "3"}
    assert transport.writes == [b"@1 sortto:3\n"]


@pytest.mark.parametrize("poll_interval", [float("inf"), float("nan"), 0.0, -0.1])
def test_request_interrupt_poll_interval_must_be_finite_and_positive(poll_interval):
    transport = ScriptedTransport()
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(ValueError, match="finite and positive"):
        client.request(
            "sortto:3",
            interrupt_requested=lambda: False,
            interrupt_poll_interval=poll_interval,
        )

    assert transport.writes == []
    assert not client.requests.active


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), 0.0, -0.1])
def test_client_default_timeout_must_be_finite_and_positive(timeout):
    with pytest.raises(ValueError, match="timeout must be finite and positive"):
        ProtocolClient(ScriptedTransport(), timeout=timeout)


@pytest.mark.parametrize("timeout", [float("inf"), float("nan"), 0.0, -0.1])
def test_request_timeout_must_be_finite_and_positive_before_transmission(timeout):
    transport = ScriptedTransport()
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(ValueError, match="timeout must be finite and positive"):
        client.request("sortto:3", timeout=timeout)

    assert transport.writes == []
    assert not client.requests.active


def test_expired_request_wins_over_new_interrupt(monkeypatch):
    ticks = iter([0.0, 0.0, 0.0, 0.02])
    monkeypatch.setattr(client_module, "monotonic", lambda: next(ticks, 0.02))
    transport = StopRespondingTransport(
        ["!1 state:mode=running phase=sort_move\n"],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(transport, timeout=.01)
    client.mode = SessionMode.V2
    interrupt_checks = 0

    def interrupt_requested() -> bool:
        nonlocal interrupt_checks
        interrupt_checks += 1
        return interrupt_checks > 1

    with pytest.raises(RecoveryError, match="timed out"):
        client.request("sortto:3", interrupt_requested=interrupt_requested)

    assert interrupt_checks == 1
    assert client.mode is SessionMode.V1
    assert transport.writes == [b"@1 sortto:3\n", b"stop\n", b"ping\n"]


def test_out_of_band_stop_clears_all_active_request_ids():
    transport = StopRespondingTransport()
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2
    client.requests.reserve(42)

    client.out_of_band_stop()

    assert not client.requests.active
    assert transport.writes == [b"stop\n"]


def test_request_timeout_clears_active_id_and_recovers_to_verified_v1():
    transport = StopRespondingTransport(reset_incoming=["Ready\n", " ok\n"])
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="timed out"):
        client.request("sortto:3", timeout=.002)

    assert not client.requests.active
    assert client.mode is SessionMode.V1
    assert transport.resets == 1
    assert transport.writes == [b"@1 sortto:3\n", b"stop\n", b"ping\n"]


@pytest.mark.parametrize(
    ("response", "crc_enabled"),
    [
        ("@1 d\x01ne\n", False),
        ("@1 unknown\n", False),
        ("@2 done\n", False),
        ("@1 done*0000\n", True),
    ],
    ids=["framing", "parse", "unexpected-id", "crc"],
)
def test_unsafe_request_response_clears_tracking_and_recovers(response, crc_enabled):
    transport = ScriptedTransport(
        [response, "stopped\n"], reset_incoming=["Ready\n", " ok\n"]
    )
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2
    client.crc_enabled = crc_enabled

    with pytest.raises(RecoveryError, match="unsafe"):
        client.request("sortto:3")

    assert not client.requests.active
    assert client.mode is SessionMode.V1
    assert transport.resets == 1
    assert transport.writes[-2:] == [b"stop\n", b"ping\n"]


def test_unexpected_exact_stopped_during_request_recovers_and_fails_closed():
    transport = ScriptedTransport(
        ["stopped\n", "stopped\n"], reset_incoming=["Ready\n", " ok\n"]
    )
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="unsafe"):
        client.request("sortto:3")

    assert not client.requests.active
    assert client.mode is SessionMode.V1
    assert transport.writes == [b"@1 sortto:3\n", b"stop\n", b"ping\n"]


class BurstTransport(ScriptedTransport):
    """Return queued lines atomically to make artificial clock tests deterministic."""

    def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
        if not self.incoming:
            return b""
        return self.incoming.popleft()


def test_continuous_events_do_not_extend_request_deadline(monkeypatch):
    ticks = iter([0.0, 0.0, 0.02])
    monkeypatch.setattr(client_module, "monotonic", lambda: next(ticks, 0.0))
    observed: list[int] = []
    transport = BurstTransport(
        ["!1 state:mode=running\n", "!2 state:mode=running\n", "stopped\n"],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(transport, timeout=.01, on_event=lambda event, _: observed.append(event.sequence))
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="timed out"):
        client.request("sortto:3")

    assert observed == [1]
    assert client.mode is SessionMode.V1


def test_recovery_stop_loop_does_not_extend_its_deadline(monkeypatch):
    class ResetFlushTransport(BurstTransport):
        def __init__(self) -> None:
            super().__init__(["noise\n", "noise\n"], reset_incoming=["Ready\n", " ok\n"])
            self.noise_reads = 0

        def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
            result = super().read(size, timeout=timeout)
            if result == b"noise\n":
                self.noise_reads += 1
            return result

        def reset(self) -> None:
            self.resets += 1
            self.incoming.clear()
            self.incoming.extend(self.reset_incoming)

    ticks = iter([0.0, 0.0, 0.02, 0.0, 0.0, 0.0])
    monkeypatch.setattr(client_module, "monotonic", lambda: next(ticks, 0.0))
    transport = ResetFlushTransport()
    client = ProtocolClient(transport, timeout=.01)

    client.recover_to_v1()

    assert transport.noise_reads == 1
    assert client.mode is SessionMode.V1


def test_failed_recovery_reports_unverified_v1_and_leaves_client_fail_closed():
    transport = ScriptedTransport(["stopped\n"], reset_incoming=["not Ready\n"])
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="did not verify a v1 session"):
        client.recover_to_v1()

    assert client.mode is SessionMode.UNCERTAIN
    with pytest.raises(ProtocolError, match="outside an active v2 session"):
        client.request("sortto:3")


def test_crc_enable_requires_its_terminal_crc_field():
    transport = ScriptedTransport(
        [append_crc("@1 data:crc=on") + "\n", append_crc("@1 done") + "\n", "stopped\n"],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="CRC enable boundary"):
        client.enable_crc()

    assert client.mode is SessionMode.V1
    assert not client.requests.active


def test_crc_disable_requires_its_terminal_crc_field():
    transport = ScriptedTransport(
        [append_crc("@1 data:crc=off") + "\n", append_crc("@1 done") + "\n", "stopped\n"],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2
    client.crc_enabled = True

    with pytest.raises(RecoveryError, match="CRC disable boundary"):
        client.disable_crc()

    assert client.mode is SessionMode.V1
    assert not client.requests.active


def test_protocol_v1_requires_its_terminal_protocol_field():
    transport = ScriptedTransport(
        ["@1 data:protocol=1\n", "@1 done\n", "stopped\n"],
        reset_incoming=["Ready\n", " ok\n"],
    )
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="v2 leave was uncertain"):
        client.leave_v2()

    assert client.mode is SessionMode.V1
    assert not client.requests.active


def test_transport_read_exception_after_request_recovers_and_clears_tracking():
    class ReadFailsUntilReset(ScriptedTransport):
        def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
            if not self.resets:
                raise OSError("disconnected")
            return super().read(size, timeout=timeout)

    transport = ReadFailsUntilReset(reset_incoming=["Ready\n", " ok\n"])
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="request response was unsafe"):
        client.request("sortto:3")

    assert client.mode is SessionMode.V1
    assert not client.requests.active
    assert transport.resets == 1


def test_write_exception_is_fail_closed_and_attempts_recovery():
    class RequestWriteFails(ScriptedTransport):
        def write(self, data: bytes) -> int:
            if data.startswith(b"@"):
                raise OSError("write status unknown")
            return super().write(data)

    transport = RequestWriteFails(["stopped\n"], reset_incoming=["Ready\n", " ok\n"])
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="request transmission failed"):
        client.request("sortto:3")

    assert client.mode is SessionMode.V1
    assert not client.requests.active
    assert transport.resets == 1


def test_reset_exception_leaves_client_uncertain_and_clears_tracking():
    class ResetFails(ScriptedTransport):
        def reset(self) -> None:
            raise OSError("reset failed")

    transport = ResetFails(["@2 done\n", "stopped\n"])
    client = ProtocolClient(transport, timeout=.02)
    client.mode = SessionMode.V2

    with pytest.raises(RecoveryError, match="v1 recovery failed"):
        client.request("sortto:3")

    assert client.mode is SessionMode.UNCERTAIN
    assert not client.requests.active

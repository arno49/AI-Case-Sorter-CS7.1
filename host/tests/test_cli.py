from __future__ import annotations

from dataclasses import dataclass

import pytest

from cs71_protocol import ScriptedTransport, append_crc
from cs71_protocol.cli import (EXIT_CHILD_LAUNCH, EXIT_DTR_GUARANTEE, EXIT_PORT_UNAVAILABLE,
                               EXIT_PROTOCOL, EXIT_RECOVERY, EXIT_SUCCESS, Dependencies, build_parser,
                               execute, main)
from cs71_protocol.locking import PortLockError


def activation_responses() -> list[str]:
    return [
        "protocol:2 ready\n", "@1 data:protocol=2\n", "@1 done\n",
        "@2 data:protocol=2 max_line=64 crc=none\n",
        "@2 data:queue_depth=2 slot_max=102 slot_count=8\n",
        "@2 data:pwm=0 airdrop=1 feed_sensor=1\n",
        "@2 data:feed_home=1 sort_home=1\n", "@2 done\n",
        "@3 data:mode=running phase=idle feed_homed=1\n",
        "@3 data:sort_homed=1 motor_enabled=0 active_id=none\n",
        "@3 data:fault_code=0 queue_previous=0 queue_next=0\n",
        "@3 data:config_generation=0\n", "@3 done\n",
    ]


def transport_for(*, available: bool = True, activate: bool = False, crc: bool = False) -> ScriptedTransport:
    discovery = "protocol:2 available\n" if available else "ok\n"
    trailing = [discovery]
    if activate:
        trailing += activation_responses()
        if crc:
            trailing += [append_crc("@4 done:crc=on") + "\n"]
    return ScriptedTransport(["stopped\n"], reset_incoming=["Ready\n", " ok\n", "CS71 1.0\n", *trailing])


@dataclass
class RecordingLock:
    events: list[str]

    def acquire(self) -> None:
        self.events.append("acquire")

    def release(self) -> None:
        self.events.append("release")


def dependencies(transport: ScriptedTransport, events: list[str] | None = None, runner=None) -> Dependencies:
    events = events if events is not None else []
    original_close = transport.close

    def close() -> None:
        events.append("close")
        original_close()

    transport.close = close  # type: ignore[method-assign]
    return Dependencies(
        opener=lambda *_args, **_kwargs: transport,
        lock_factory=lambda _port: RecordingLock(events),
        runner=runner or (lambda argv: type("Child", (), {"returncode": 0})()),
    )


def parse(*argv: str):
    return build_parser().parse_args(argv)


def test_detect_distinguishes_old_firmware_and_never_activates_it():
    transport = transport_for(available=False)
    code, result = execute(parse("detect", "--port", "fake"), dependencies(transport))

    assert code == EXIT_SUCCESS
    assert result["firmware"] == "v1-only"
    assert transport.writes == [b"stop\n", b"ping\n", b"version\n", b"protocol:2?\n"]
    assert b"protocol:2\n" not in transport.writes


def test_enter_v2_validates_and_can_enable_crc():
    transport = transport_for(activate=True, crc=True)
    code, result = execute(parse("enter-v2", "--port", "fake", "--crc"), dependencies(transport))

    assert code == EXIT_SUCCESS
    assert result["mode"] == "v2" and result["crc"] is True
    assert transport.writes[-4:] == [
        b"@1 protocolversion\n", b"@2 capabilities\n", b"@3 status\n", b"@4 crc:on\n",
    ]


def test_leave_uses_safe_fallback_from_a_fresh_process():
    transport = ScriptedTransport(["stopped\n"], reset_incoming=["Ready\n", " ok\n"])
    code, result = execute(parse("leave-v2", "--port", "fake"), dependencies(transport))

    assert code == EXIT_SUCCESS
    assert result == {"mode": "v1", "method": "safe-stop-reset-fallback"}
    assert transport.writes == [b"stop\n", b"ping\n"]


def test_prepare_closes_then_releases_and_reports_volatile_settings():
    events: list[str] = []
    transport = ScriptedTransport(["stopped\n"], reset_incoming=["Ready\n", " ok\n"])
    code, result = execute(parse("prepare-legacy", "--port", "fake"), dependencies(transport, events))

    assert code == EXIT_SUCCESS and result["volatile_settings_reset"] is True
    assert events == ["acquire", "close", "release"]


@pytest.mark.parametrize(
    ("failure", "reset_incoming"),
    [
        ("reset", ()),
        ("ready-timeout", ()),
        ("ready-malformed", (b"\xff\n",)),
        ("ready-missing", ("not Ready\n",)),
        ("ping-write", ("Ready\n", " ok\n")),
        ("ping-read", ("Ready\n", " ok\n")),
        ("ping-response", ("Ready\n", "not ok\n")),
    ],
)
@pytest.mark.parametrize(
    "argv",
    [
        ("detect", "--port", "fake", "--timeout", "0.005"),
        ("enter-v2", "--port", "fake", "--timeout", "0.005"),
        ("leave-v2", "--port", "fake", "--timeout", "0.005"),
        ("prepare-legacy", "--port", "fake", "--timeout", "0.005"),
        ("run-legacy", "--port", "fake", "--timeout", "0.005", "--", "legacy-app"),
    ],
)
def test_reset_barrier_failures_leave_every_cli_operation_uncertain(failure, reset_incoming, argv):
    class FailingBarrierTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__(["stopped\n"], reset_incoming=reset_incoming)

        def reset(self) -> None:
            if failure == "reset":
                raise OSError("reset failed")
            super().reset()

        def write(self, data: bytes) -> int:
            if failure == "ping-write" and self.resets and data == b"ping\n":
                raise OSError("ping write failed")
            return super().write(data)

        def read(self, size: int = 1, *, timeout: float | None = None) -> bytes:
            if failure == "ping-read" and self.resets and b"ping\n" in self.writes:
                raise OSError("ping read failed")
            return super().read(size, timeout=timeout)

    launched: list[object] = []
    transport = FailingBarrierTransport()
    code, result = execute(
        parse(*argv),
        dependencies(transport, runner=lambda _argv: launched.append(object())),
    )

    assert code == EXIT_RECOVERY
    assert result == {"error": "reset did not verify a v1 session", "mode": "uncertain"}
    assert launched == []


def test_run_releases_before_launches_and_preserves_argv_and_child_exit():
    events: list[str] = []
    transport = ScriptedTransport(["stopped\n"], reset_incoming=["Ready\n", " ok\n"])
    launched: list[list[str]] = []

    def runner(argv):
        events.append("run")
        launched.append(list(argv))
        return type("Child", (), {"returncode": 19})()

    code, result = execute(
        parse("run-legacy", "--port", "fake", "--", "legacy app", "--flag", "two words"),
        dependencies(transport, events, runner),
    )

    assert code == 19 and result["child_exit"] == 19
    assert launched == [["legacy app", "--flag", "two words"]]
    assert events == ["acquire", "close", "release", "run"]


def test_run_legacy_nonzero_child_exit_is_not_rendered_as_a_tool_error(capsys):
    transport = ScriptedTransport(["stopped\n"], reset_incoming=["Ready\n", " ok\n"])
    code = main(
        ["run-legacy", "--port", "fake", "--", "legacy-app"],
        dependencies=dependencies(
            transport, runner=lambda _argv: type("Child", (), {"returncode": 19})()
        ),
    )
    assert code == 19
    assert capsys.readouterr().err == "legacy application exited with status 19\n"

    transport = ScriptedTransport(["stopped\n"], reset_incoming=["Ready\n", " ok\n"])
    code = main(
        ["--json", "run-legacy", "--port", "fake", "--", "legacy-app"],
        dependencies=dependencies(
            transport, runner=lambda _argv: type("Child", (), {"returncode": 19})()
        ),
    )
    assert code == 19
    output = capsys.readouterr().out
    assert '"child_exit": 19' in output and '"exit_code": 19' in output and '"error"' not in output


def test_timeout_contention_and_dtr_limitations_have_stable_exit_codes():
    timeout_transport = ScriptedTransport(["stopped\n"])
    code, result = execute(
        parse("detect", "--port", "fake", "--timeout", "0.001"), dependencies(timeout_transport)
    )
    assert code == EXIT_RECOVERY and result["mode"] == "uncertain"

    class Contended:
        def acquire(self):
            raise PortLockError("busy")

        def release(self):
            raise AssertionError("must not release an unacquired lock")

    code, _ = execute(
        parse("detect", "--port", "fake"),
        Dependencies(opener=lambda *_args, **_kwargs: ScriptedTransport(), lock_factory=lambda _: Contended()),
    )
    assert code == EXIT_PORT_UNAVAILABLE

    unsafe = transport_for()
    unsafe.dtr_suppression_guaranteed = False
    code, result = execute(parse("detect", "--port", "fake"), dependencies(unsafe))
    assert code == EXIT_DTR_GUARANTEE and result["dtr_suppression_guaranteed"] is False
    assert unsafe.writes == []


def test_no_reset_is_rejected_before_locking_or_opening():
    events: list[str] = []

    class MustNotUse:
        def acquire(self):
            events.append("acquire")
            raise AssertionError("must not acquire lock")

        def release(self):
            raise AssertionError("must not release lock")

    code, result = execute(
        parse("detect", "--port", "fake", "--no-reset"),
        Dependencies(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not open")),
                     lock_factory=lambda _port: MustNotUse()),
    )
    assert code == EXIT_DTR_GUARANTEE
    assert result["safety_guarantee"] is False
    assert events == []


@pytest.mark.parametrize("timeout", ["nan", "inf", "-inf", "0"])
def test_nonfinite_or_nonpositive_timeout_is_usage_before_locking(timeout):
    class MustNotUse:
        def acquire(self):
            raise AssertionError("must not acquire lock")

        def release(self):
            raise AssertionError("must not release lock")

    code, result = execute(
        parse("detect", "--port", "fake", f"--timeout={timeout}"),
        Dependencies(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not open")),
                     lock_factory=lambda _port: MustNotUse()),
    )
    assert code == 2
    assert result["error"] == "--timeout must be finite and greater than zero"


def test_successful_recovery_is_a_protocol_failure_not_uncertain_recovery():
    class MultiResetTransport(ScriptedTransport):
        def __init__(self) -> None:
            super().__init__(["stopped\n"])
            self.reset_sequences = [
                [
                    "Ready\n", " ok\n", "CS71 1.0\n", "protocol:2 available\n",
                    "protocol:2 ready\n", "@1 data:protocol=1\n", "@1 done\n", "stopped\n",
                ],
                ["Ready\n", " ok\n"],
            ]

        def reset(self) -> None:
            self.resets += 1
            self.incoming.extend(item.encode() for item in self.reset_sequences[self.resets - 1])

    code, result = execute(
        parse("enter-v2", "--port", "fake"),
        dependencies(MultiResetTransport()),
    )
    assert code == EXIT_PROTOCOL
    assert result["mode"] == "v1" and result["recovered"] is True


def test_child_launch_failure_json_and_usage_exit_codes(capsys):
    transport = ScriptedTransport(["stopped\n"], reset_incoming=["Ready\n", " ok\n"])

    def fail(_argv):
        raise OSError("not found")

    assert main(
        ["--json", "run-legacy", "--port", "fake", "--", "missing"],
        dependencies=dependencies(transport, runner=fail),
    ) == EXIT_CHILD_LAUNCH
    assert '"exit_code": 7' in capsys.readouterr().out
    assert main(["detect", "--port", "fake", "--json"], dependencies=dependencies(transport_for())) == EXIT_SUCCESS
    assert '"v2_available": true' in capsys.readouterr().out
    assert main(["--json", "detect"]) == 2
    assert '"exit_code": 2' in capsys.readouterr().out

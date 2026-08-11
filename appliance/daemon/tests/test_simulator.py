import logging
from concurrent.futures import ThreadPoolExecutor

from _pytest.logging import LogCaptureFixture
from cs71_protocol import Completion, ProtocolClient, SessionMode, append_crc, parse_v2_line
from cs71_protocol.models import Event, Response, ResponseKind

from cs71d.simulator import (
    SIMULATOR_EVIDENCE_CLASS,
    SimulatorConfig,
    SimulatorMode,
    SimulatorTransport,
)


def _activate(config: SimulatorConfig | None = None) -> tuple[SimulatorTransport, ProtocolClient]:
    simulator = SimulatorTransport(config)
    client = ProtocolClient(simulator, timeout=0.02)
    client.wait_ready()
    client.v1_ping_barrier()
    assert client.activate()
    return simulator, client


def _request_with_advance(
    simulator: SimulatorTransport,
    client: ProtocolClient,
    command: str,
) -> Completion:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.request, command, timeout=1.0)
        assert simulator.wait_until_scheduled(timeout=0.5)
        simulator.advance(10_000)
        completion = future.result(timeout=0.5)
    return completion


def _activate_running(
    config: SimulatorConfig | None = None,
) -> tuple[SimulatorTransport, ProtocolClient]:
    simulator, client = _activate(config)
    assert _request_with_advance(simulator, client, "homeall").succeeded
    return simulator, client


def test_v2_activation_queries_use_real_protocol_client() -> None:
    simulator, client = _activate()

    assert simulator.mode is SimulatorMode.V2
    assert client.mode is SessionMode.V2
    assert client.status is not None
    assert client.status.mode == "recovering"
    assert client.status.phase == "idle"
    assert client.status.feed_homed is False
    assert client.status.sort_homed is False
    capabilities = client.get_capabilities()
    assert capabilities.protocol == 2
    assert capabilities.crc == "optional"
    queue = client.get_queue()
    assert queue.queue_previous == 0
    assert queue.queue_next == 0


def test_legacy_scenario_never_enters_v2() -> None:
    simulator = SimulatorTransport(
        SimulatorConfig(scenario="legacy-v1", v2_available=False, crc_available=False)
    )
    client = ProtocolClient(simulator, timeout=0.02)
    client.wait_ready()
    client.v1_ping_barrier()

    assert client.activate() is False
    assert simulator.mode is SimulatorMode.V1
    assert simulator.crc_enabled is False


def test_crc_enable_query_disable_boundaries() -> None:
    simulator, client = _activate()

    enabled = client.enable_crc()
    assert enabled.succeeded
    assert simulator.crc_enabled
    assert client.get_queue().queue_depth == 2
    disabled = client.disable_crc()
    assert disabled.succeeded
    assert simulator.crc_enabled is False


def test_motion_terminal_requires_explicit_clock_advance() -> None:
    simulator, _ = _activate_running(SimulatorConfig(operation_duration_ms=25))
    started_at_ms = simulator.clock.now_ms

    simulator.write(b"@40 sortto:3\n")
    immediate = simulator.drain_output().decode("ascii").splitlines()
    assert immediate[0] == "@40 accepted:operation=sort"
    assert isinstance(parse_v2_line(immediate[0]), Response)
    assert isinstance(parse_v2_line(immediate[1]), Event)
    assert all("done:" not in line for line in immediate)
    assert simulator.clock.now_ms == started_at_ms

    simulator.advance(24)
    assert simulator.drain_output() == b""
    simulator.advance(1)
    terminal = simulator.drain_output().decode("ascii").splitlines()
    assert isinstance(parse_v2_line(terminal[0]), Event)
    completion = parse_v2_line(terminal[1])
    assert isinstance(completion, Response)
    assert completion.kind is ResponseKind.DONE
    assert completion.fields["slot"] == "3"


def test_protocol_client_motion_completes_after_explicit_advance() -> None:
    simulator, client = _activate_running(SimulatorConfig(operation_duration_ms=25))

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.request, "sortto:3", timeout=1.0)
        assert simulator.wait_until_scheduled(timeout=0.5)
        assert not future.done()
        simulator.advance(25)
        completion = future.result(timeout=0.5)

    assert completion.succeeded
    assert completion.terminal_fields["slot"] == "3"


def test_seed_and_scenario_produce_identical_transcripts() -> None:
    config = SimulatorConfig(
        scenario="seeded-happy",
        seed=917,
        operation_duration_ms=20,
        operation_jitter_ms=15,
    )

    def run() -> tuple[object, ...]:
        simulator, _ = _activate_running(config)
        simulator.write(b"@40 sortto:7\n")
        simulator.advance(100)
        return simulator.transcript

    assert run() == run()


def test_reset_restores_v1_and_startup_marker() -> None:
    simulator, client = _activate()
    client.enable_crc()

    simulator.reset()

    assert simulator.mode is SimulatorMode.V1
    assert simulator.crc_enabled is False
    assert simulator.drain_output() == b"Ready\n"


def test_priority_stop_is_exact_unframed_terminal() -> None:
    simulator, client = _activate_running()
    client.enable_crc()
    simulator.write(append_crc("@40 sortto:3").encode("ascii") + b"\n")
    simulator.drain_output()

    simulator.write(b"stop\n")

    output = simulator.drain_output().decode("ascii").splitlines()
    event = parse_v2_line(output[0], crc_required=True)
    assert isinstance(event, Event)
    assert event.fields == {"mode": "stopped", "phase": "idle"}
    assert output[1] == "stopped"
    assert simulator.phase == "idle"
    assert simulator.clock.pending_count == 0
    assert simulator.dtr_suppression_guaranteed is False


def test_active_motion_rejects_another_state_change() -> None:
    simulator, _ = _activate_running()
    simulator.write(b"@40 sortto:3\n")
    simulator.drain_output()

    simulator.write(b"@41 protocol:1\n")

    response = parse_v2_line(simulator.drain_output().decode("ascii").strip())
    assert isinstance(response, Response)
    assert response.kind is ResponseKind.ERROR
    assert response.code == 2001
    assert simulator.mode is SimulatorMode.V2


def test_correlated_stop_cancels_active_request_in_normative_order() -> None:
    simulator, client = _activate_running()
    simulator.write(b"@40 sortto:3\n")
    simulator.drain_output()

    simulator.write(b"@99 stop\n")
    lines = simulator.drain_output().decode("ascii").splitlines()

    cancelled = parse_v2_line(lines[0])
    state = parse_v2_line(lines[1])
    stopped = parse_v2_line(lines[2])
    assert isinstance(cancelled, Response)
    assert cancelled.request_id == 40
    assert cancelled.kind is ResponseKind.ERROR
    assert cancelled.code == 2004
    assert cancelled.fields["by"] == "99"
    assert isinstance(state, Event)
    assert state.fields == {"mode": "stopped", "phase": "idle"}
    assert isinstance(stopped, Response)
    assert stopped.request_id == 99
    assert stopped.kind is ResponseKind.DONE

    status = client.get_status()
    assert status.mode == "stopped"
    assert status.feed_homed is False
    assert status.sort_homed is False


def test_single_axis_home_remains_recovering_after_stop() -> None:
    simulator, client = _activate_running()
    simulator.write(b"stop\n")
    simulator.drain_output()

    completion = _request_with_advance(simulator, client, "homefeeder")

    assert completion.succeeded
    assert completion.terminal_fields == {"feed_homed": "1", "sort_homed": "0"}
    status = client.get_status()
    assert status.mode == "recovering"
    assert status.feed_homed is True
    assert status.sort_homed is False


def test_backend_identity_is_conspicuous_in_logs(caplog: LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING, logger="cs71d.simulator")

    simulator = SimulatorTransport(SimulatorConfig(scenario="identity", seed=42))

    assert simulator.backend_identity.startswith(f"{SIMULATOR_EVIDENCE_CLASS}:")
    assert SIMULATOR_EVIDENCE_CLASS in caplog.text

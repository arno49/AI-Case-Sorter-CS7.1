from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from cs71_protocol import Completion, ProtocolClient, RecoveryError, SessionMode

from cs71d.simulator import (
    AdverseScenario,
    FixtureExchange,
    FixtureReplayError,
    FixtureReplayTransport,
    ReplayAction,
    ReplayStep,
    SimulatorConfig,
    SimulatorTransport,
    TranscriptDirection,
    load_line_fixture,
    load_v1_wire_fixture,
)

REPOSITORY_ROOT = Path(__file__).parents[3]


def _activate(
    config: SimulatorConfig | None = None,
) -> tuple[SimulatorTransport, ProtocolClient]:
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
        return future.result(timeout=0.5)


def _activate_running(
    scenario: AdverseScenario,
) -> tuple[SimulatorTransport, ProtocolClient]:
    simulator, client = _activate(SimulatorConfig(scenario=scenario))
    assert _request_with_advance(simulator, client, "homeall").succeeded
    return simulator, client


def test_legacy_golden_fixture_replays_through_protocol_client() -> None:
    exchanges = load_v1_wire_fixture(REPOSITORY_ROOT / "test/fixtures/v1-wire-golden.txt")

    def exchange_for(
        *,
        section: str,
        request: bytes | None = None,
        marker: str | None = None,
    ) -> FixtureExchange:
        return next(
            exchange
            for exchange in exchanges
            if exchange.section == section
            and exchange.request == request
            and exchange.request_marker == marker
        )

    replay = FixtureReplayTransport(
        [
            exchange_for(section="startup", marker="<reset>").to_replay_step(),
            exchange_for(section="inspection", request=b"ping\n").to_replay_step(),
            exchange_for(
                section="negotiation-on-old-firmware", request=b"protocol:2?\n"
            ).to_replay_step(),
        ]
    )
    client = ProtocolClient(replay, timeout=0.02)

    replay.reset()
    client.wait_ready()
    client.v1_ping_barrier()

    assert client.activate() is False
    replay.assert_complete()


def test_legacy_stop_fixture_replays_through_protocol_client() -> None:
    exchanges = load_v1_wire_fixture(REPOSITORY_ROOT / "test/fixtures/v1-wire-golden.txt")
    stop = next(
        exchange
        for exchange in exchanges
        if exchange.section == "commands" and exchange.request == b"stop\n"
    )
    replay = FixtureReplayTransport([stop.to_replay_step()])
    client = ProtocolClient(replay, timeout=0.02)

    client.out_of_band_stop()

    replay.assert_complete()


def test_v2_golden_fixture_replays_status_with_forward_fields() -> None:
    lines = load_line_fixture(REPOSITORY_ROOT / "host/tests/fixtures/v2-session-trace.txt")
    assert lines[:2] == (b"protocol:2 available\r\n", b"protocol:2 ready\n")
    replay = FixtureReplayTransport([ReplayStep(ReplayAction.WRITE, b"@1 status\n", lines[2:])])
    client = ProtocolClient(replay, timeout=0.02)
    client.mode = SessionMode.V2

    status = client.get_status()

    assert status.mode == "running"
    assert status.queue_next == 3
    assert status.extras == {"future": "x"}
    replay.assert_complete()


def test_fixture_replay_rejects_host_byte_drift() -> None:
    replay = FixtureReplayTransport([ReplayStep(ReplayAction.WRITE, b"ping\n", (b" ok\n",))])

    with pytest.raises(FixtureReplayError, match="fixture expected"):
        replay.write(b"ping\r\n")


def test_symbolic_timing_fixture_is_not_replayed_as_wire_bytes() -> None:
    exchanges = load_v1_wire_fixture(REPOSITORY_ROOT / "test/fixtures/v1-wire-golden.txt")
    delayed_feed = next(
        exchange
        for exchange in exchanges
        if exchange.section == "state-and-queue" and exchange.request == b"1\n"
    )

    assert delayed_feed.response is None
    assert delayed_feed.response_marker == "<none until physical feed completion>,done\\n"
    with pytest.raises(ValueError, match="symbolic fixture exchange"):
        delayed_feed.to_replay_step()


def test_feed_overtravel_scenario_emits_correlated_fault_and_latches_status() -> None:
    simulator, client = _activate_running(AdverseScenario.FAULT)

    completion = _request_with_advance(simulator, client, "sortto:3")

    assert completion.succeeded is False
    assert completion.error is not None
    assert completion.error.code == 3001
    terminal_index = next(
        index
        for index, entry in enumerate(simulator.transcript)
        if entry.direction is TranscriptDirection.SIMULATOR_TO_HOST
        and b" error:3001:feed_overtravel\n" in entry.data
    )
    fault_frames = tuple(
        entry.data for entry in simulator.transcript[terminal_index : terminal_index + 3]
    )
    assert fault_frames[0] == (
        f"@{completion.request_id} error:3001:feed_overtravel\n".encode("ascii")
    )
    assert fault_frames[1].split(b" ", 1)[1] == b"fault:3001:feed_overtravel latched=1\n"
    assert fault_frames[2].split(b" ", 1)[1] == b"state:mode=recovering phase=idle\n"
    status = client.get_status()
    assert status.fault_code == 3001
    assert status.feed_homed is False


def test_event_gap_scenario_requires_status_resynchronization() -> None:
    observed: list[bool] = []
    simulator = SimulatorTransport(SimulatorConfig(scenario=AdverseScenario.EVENT_GAP))
    client = ProtocolClient(
        simulator,
        timeout=0.02,
        on_event=lambda _event, contiguous: observed.append(contiguous),
    )
    client.wait_ready()
    client.v1_ping_barrier()
    assert client.activate()

    completion = client.request("ping")

    assert completion.succeeded
    assert observed == [True, False]
    assert client.events.resync_required is True
    client.get_status()
    assert client.events.resync_required is False


@pytest.mark.parametrize(
    ("scenario", "message"),
    [
        (AdverseScenario.DISCONNECT, "unsafe"),
        (AdverseScenario.MALFORMED_FRAME, "unsafe"),
        (AdverseScenario.TERMINAL_MISMATCH, "unsafe"),
        (AdverseScenario.TIMEOUT, "timed out"),
    ],
)
def test_unsafe_scenarios_fail_closed_and_recover_to_v1(
    scenario: AdverseScenario,
    message: str,
) -> None:
    simulator, client = _activate_running(scenario)
    started_at_ms = simulator.clock.now_ms
    transcript_start = len(simulator.transcript)

    with pytest.raises(RecoveryError, match=message) as raised:
        client.request("sortto:3", timeout=0.005)

    interaction = simulator.transcript[transcript_start:]
    sort_index = next(
        index
        for index, entry in enumerate(interaction)
        if entry.direction is TranscriptDirection.HOST_TO_SIMULATOR and b" sortto:3\n" in entry.data
    )
    stop_index = next(
        index
        for index, entry in enumerate(interaction[sort_index + 1 :], sort_index + 1)
        if entry.direction is TranscriptDirection.HOST_TO_SIMULATOR and entry.data == b"stop\n"
    )
    injected_output = tuple(
        entry.data
        for entry in interaction[sort_index + 1 : stop_index]
        if entry.direction is TranscriptDirection.SIMULATOR_TO_HOST
    )
    request_id = int(interaction[sort_index].data.split(b" ", 1)[0][1:])
    if scenario is AdverseScenario.MALFORMED_FRAME:
        assert injected_output == (f"@{request_id} d\x01ne\n".encode("ascii"),)
    elif scenario is AdverseScenario.TERMINAL_MISMATCH:
        assert injected_output == (f"@{request_id + 1} done:slot=3\n".encode("ascii"),)
    else:
        assert injected_output == ()

    assert raised.value.recovered is True
    assert client.mode is SessionMode.V1
    assert not client.requests.active
    assert simulator.clock.now_ms == started_at_ms

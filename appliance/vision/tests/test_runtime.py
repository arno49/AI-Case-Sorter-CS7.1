from __future__ import annotations

import json
import shutil
import socketserver
import tempfile
import threading
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from cs71vision.api import VisionApiServer
from cs71vision.autonomy import Autonomist
from cs71vision.camera import CameraError, Frame
from cs71vision.classifier import FrameSuggester
from cs71vision.config import Backend, ConfigError, Profile, VisionConfig
from cs71vision.correlator import FrameBuffer
from cs71vision.daemon_client import DaemonClient
from cs71vision.dataset import IN_MEMORY, DatasetExample, DatasetStore, TrainedCandidate
from cs71vision.runtime import (
    AutonomyLoop,
    CaptureLoop,
    CorrelationLoop,
    SuggestionLoop,
    TrainingJob,
    build_api_server,
    build_autonomy_loop,
    build_camera,
    build_correlation_loop,
    build_suggestion_loop,
    build_training_job,
    read_service_token,
)
from cs71vision.training import TrainingError, train_candidate

START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def _png(value: int, *, size: int = 4) -> bytes:
    image = np.full((size, size, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _wait_until_not_running(job: TrainingJob, *, timeout: float = 2.0) -> None:
    """Wait for a triggered run to finish without closing the job's store.

    `TrainingJob.close()` releases the store as well as joining the thread
    (the same shape `CorrelationLoop.close()` uses), so a test that still
    wants to inspect the store afterward waits this way instead.
    """
    deadline = time.monotonic() + timeout
    while job.running:
        if time.monotonic() > deadline:
            raise AssertionError("the training job never finished")
        time.sleep(0.01)


class CountingCamera:
    """A `Camera` the test can inspect and, on demand, make fail."""

    def __init__(self) -> None:
        self.reads = 0
        self.closed = False
        self.fail_next = False

    def read(self) -> Frame:
        self.reads += 1
        if self.fail_next:
            self.fail_next = False
            raise CameraError("injected read failure")
        return Frame(png=b"\x89PNG", width=1, height=1, captured_at=START)

    def close(self) -> None:
        self.closed = True


class RecordingSink:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self.frames: list[Frame] = []

    def __call__(self, frame: Frame) -> None:
        with self._condition:
            self.frames.append(frame)
            self._condition.notify_all()

    def wait_for_count(self, count: int, *, timeout: float = 2.0) -> None:
        with self._condition:
            if not self._condition.wait_for(lambda: len(self.frames) >= count, timeout):
                raise AssertionError(f"only {len(self.frames)} of {count} frames arrived")


def test_capture_loop_delivers_frames_to_the_sink() -> None:
    camera = CountingCamera()
    sink = RecordingSink()
    loop = CaptureLoop(camera, interval_ms=1, sink=sink)

    loop.start()
    try:
        sink.wait_for_count(3)
    finally:
        loop.close()

    assert camera.closed


def test_capture_loop_survives_a_camera_read_failure() -> None:
    camera = CountingCamera()
    camera.fail_next = True
    sink = RecordingSink()
    loop = CaptureLoop(camera, interval_ms=1, sink=sink)

    loop.start()
    try:
        sink.wait_for_count(1)
    finally:
        loop.close()

    # The failed read did not stop the loop from trying again.
    assert camera.reads >= 2


def test_capture_loop_survives_a_sink_that_raises() -> None:
    camera = CountingCamera()
    reads_seen = threading.Event()

    def bad_sink(frame: Frame) -> None:
        reads_seen.set()
        raise RuntimeError("sink exploded")

    loop = CaptureLoop(camera, interval_ms=1, sink=bad_sink)

    loop.start()
    try:
        if not reads_seen.wait(2.0):
            raise AssertionError("the sink was never called")
    finally:
        loop.close()

    assert camera.reads >= 1
    assert camera.closed


def test_capture_loop_refuses_a_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        CaptureLoop(CountingCamera(), interval_ms=0)


def test_starting_an_already_started_loop_does_not_spawn_a_second_thread() -> None:
    camera = CountingCamera()
    sink = RecordingSink()
    loop = CaptureLoop(camera, interval_ms=1, sink=sink)

    loop.start()
    try:
        sink.wait_for_count(1)
        thread_count_before = threading.active_count()
        loop.start()  # a second thread here would leave two loops reading one camera
        assert threading.active_count() == thread_count_before
    finally:
        loop.close()


def test_capture_loop_as_a_context_manager_starts_and_closes() -> None:
    camera = CountingCamera()
    sink = RecordingSink()

    with CaptureLoop(camera, interval_ms=1, sink=sink):
        sink.wait_for_count(1)

    assert camera.closed


def test_build_camera_returns_a_fixture_camera_for_the_fixture_backend() -> None:
    config = VisionConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.FIXTURE,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path="/tmp/cs71/cs71d.sock",
        dataset_path="/tmp/cs71-vision/vision.db",
    )

    camera = build_camera(config)

    frame = camera.read()
    assert frame.width == 4
    assert frame.height == 4
    camera.close()


def test_build_camera_refuses_a_v4l2_backend_without_a_device_path() -> None:
    config = VisionConfig(
        profile=Profile.PRODUCTION,
        backend=Backend.V4L2,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path="/tmp/cs71/cs71d.sock",
        dataset_path="/tmp/cs71-vision/vision.db",
    )

    with pytest.raises(ValueError, match="device_path"):
        build_camera(config)


class CountingCorrelator:
    def __init__(self) -> None:
        self.calls = 0

    def poll_once(self) -> int:
        self.calls += 1
        return 0


class ExplodingCorrelator:
    def poll_once(self) -> int:
        raise RuntimeError("poll exploded")


def test_correlation_loop_polls_repeatedly() -> None:
    correlator = CountingCorrelator()
    store = DatasetStore.open(IN_MEMORY)
    loop = CorrelationLoop(correlator, store, interval_ms=1)

    loop.start()
    try:
        deadline = threading.Event()
        deadline.wait(0.05)
    finally:
        loop.close()

    assert correlator.calls >= 1


def test_correlation_loop_survives_a_poll_that_raises() -> None:
    store = DatasetStore.open(IN_MEMORY)
    loop = CorrelationLoop(ExplodingCorrelator(), store, interval_ms=1)

    loop.start()
    deadline = threading.Event()
    deadline.wait(0.05)
    loop.close()  # must not raise or hang


def test_correlation_loop_closes_its_store() -> None:
    correlator = CountingCorrelator()
    store = DatasetStore.open(IN_MEMORY)
    loop = CorrelationLoop(correlator, store, interval_ms=1000)

    loop.start()
    loop.close()

    with pytest.raises(Exception, match="closed"):
        store.total_examples()


def test_correlation_loop_refuses_a_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        CorrelationLoop(CountingCorrelator(), DatasetStore.open(IN_MEMORY), interval_ms=0)


@pytest.fixture
def token_workspace() -> Iterator[Path]:
    directory = Path(tempfile.mkdtemp(prefix="cs71vision-token"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _token_file(
    directory: Path,
    *,
    name: str = "service-token",
    mode: int = 0o600,
    content: str = "a-real-token",
) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_read_service_token_requires_an_existing_readable_file(token_workspace: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        read_service_token(token_workspace / "absent")


def test_read_service_token_refuses_a_world_readable_file(token_workspace: Path) -> None:
    world_readable = _token_file(token_workspace, mode=0o644)

    with pytest.raises(ConfigError, match="readable by other users"):
        read_service_token(world_readable)


def test_read_service_token_refuses_an_empty_file(token_workspace: Path) -> None:
    empty = _token_file(token_workspace, content="   ")

    with pytest.raises(ConfigError, match="is empty"):
        read_service_token(empty)


def test_read_service_token_returns_the_stripped_content(token_workspace: Path) -> None:
    path = _token_file(token_workspace, content="a-real-token\n")

    assert read_service_token(path) == "a-real-token"


def test_build_correlation_loop_is_none_without_a_token_path() -> None:
    config = VisionConfig.development()

    assert build_correlation_loop(config, FrameBuffer()) is None


def test_build_correlation_loop_builds_a_working_loop_given_a_token_path(
    token_workspace: Path,
) -> None:
    token_path = _token_file(token_workspace)
    config = VisionConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.FIXTURE,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path=str(token_workspace / "cs71d.sock"),
        dataset_path=IN_MEMORY,
        daemon_service_token_path=str(token_path),
    )

    loop = build_correlation_loop(config, FrameBuffer())

    assert loop is not None
    assert isinstance(loop, CorrelationLoop)
    loop.close()


def test_build_api_server_is_none_without_a_token_path() -> None:
    config = VisionConfig.development()

    assert build_api_server(config) is None


def test_build_api_server_builds_a_working_server_given_a_token_path(
    token_workspace: Path,
) -> None:
    token_path = _token_file(token_workspace)
    config = VisionConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.FIXTURE,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path=str(token_workspace / "cs71d.sock"),
        dataset_path=IN_MEMORY,
        daemon_service_token_path=str(token_path),
        api_socket_path=str(token_workspace / "cs71vision.sock"),
        minimum_examples_per_class=5,
    )

    server = build_api_server(config)

    assert server is not None
    assert isinstance(server, VisionApiServer)
    server.close()


def _candidate(**overrides: object) -> TrainedCandidate:
    defaults: dict[str, object] = {
        "model_blob": b"model",
        "included_classes": (3, 5),
        "excluded_classes": (),
        "accuracy_by_class": {3: 1.0, 5: 1.0},
        "minimum_examples_per_class": 5,
        "training_example_count": 10,
        "holdout_example_count": 2,
    }
    defaults.update(overrides)
    return TrainedCandidate(**defaults)  # type: ignore[arg-type]


class RecordingTrainer:
    """A `Trainer` the test can inspect: what it was called with, what it returns."""

    def __init__(
        self, candidate: TrainedCandidate | None = None, *, error: Exception | None = None
    ) -> None:
        self.calls: list[tuple[DatasetExample, ...]] = []
        self._candidate = candidate if candidate is not None else _candidate()
        self._error = error

    def __call__(self, examples: Sequence[DatasetExample]) -> TrainedCandidate:
        self.calls.append(tuple(examples))
        if self._error is not None:
            raise self._error
        return self._candidate


class BlockingTrainer:
    """A `Trainer` that blocks until released, so a test can observe it mid-run."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def __call__(self, examples: Sequence[DatasetExample]) -> TrainedCandidate:
        self.started.set()
        if not self.release.wait(2.0):
            raise AssertionError("the test never released the blocking trainer")
        return _candidate()


def test_trigger_runs_the_trainer_on_the_current_examples_and_records_the_result() -> None:
    store = DatasetStore.open(IN_MEMORY, now=lambda: START)
    store.record_example(
        operation_id="op-1",
        slot=3,
        frame_png=b"x",
        frame_captured_at=START,
        operation_created_at=START,
    )
    trainer = RecordingTrainer(_candidate(model_blob=b"trained-model"))
    job = TrainingJob(store, trainer, now=lambda: START)

    assert job.trigger() is True
    _wait_until_not_running(job)

    assert trainer.calls == [(DatasetExample(slot=3, frame_png=b"x"),)]
    [summary] = store.candidates()
    assert summary.trained_at == START.isoformat()
    job.close()


def test_trigger_returns_false_while_a_run_is_already_in_flight() -> None:
    store = DatasetStore.open(IN_MEMORY)
    trainer = BlockingTrainer()
    job = TrainingJob(store, trainer)

    assert job.trigger() is True
    assert trainer.started.wait(2.0)
    assert job.trigger() is False

    trainer.release.set()
    job.close()


def test_running_reflects_an_in_flight_run_only() -> None:
    store = DatasetStore.open(IN_MEMORY)
    trainer = BlockingTrainer()
    job = TrainingJob(store, trainer)

    assert job.running is False
    job.trigger()
    assert trainer.started.wait(2.0)
    assert job.running is True

    trainer.release.set()
    job.close()
    assert job.running is False


def test_a_training_error_records_nothing_and_does_not_crash() -> None:
    store = DatasetStore.open(IN_MEMORY)
    trainer = RecordingTrainer(error=TrainingError("not enough classes"))
    job = TrainingJob(store, trainer)

    job.trigger()
    _wait_until_not_running(job)  # must not raise or hang

    assert store.candidates() == ()
    job.close()


def test_an_unexpected_trainer_failure_does_not_crash_the_job() -> None:
    store = DatasetStore.open(IN_MEMORY)
    trainer = RecordingTrainer(error=RuntimeError("the classifier library blew up"))
    job = TrainingJob(store, trainer)

    job.trigger()
    _wait_until_not_running(job)  # must not raise or hang

    assert store.candidates() == ()
    job.close()


def test_close_waits_for_an_in_flight_run_to_finish_before_returning(tmp_path: Path) -> None:
    db_path = tmp_path / "vision.db"
    store = DatasetStore.open(db_path)
    trainer = BlockingTrainer()
    job = TrainingJob(store, trainer)
    job.trigger()
    assert trainer.started.wait(2.0)

    def release_shortly() -> None:
        time.sleep(0.05)
        trainer.release.set()

    threading.Thread(target=release_shortly, daemon=True).start()
    job.close()

    # close() only returns after the thread it joined has finished and the
    # store it owned is released - reopening it here is the proof the
    # candidate that thread recorded actually reached disk before close()
    # returned control to this test.
    reopened = DatasetStore.open(db_path)
    try:
        assert len(reopened.candidates()) == 1
    finally:
        reopened.close()


def test_close_releases_the_store() -> None:
    store = DatasetStore.open(IN_MEMORY)
    job = TrainingJob(store, RecordingTrainer())

    job.close()

    with pytest.raises(Exception, match="closed"):
        store.total_examples()


def test_reading_the_store_is_not_blocked_while_a_training_run_is_in_flight() -> None:
    store = DatasetStore.open(IN_MEMORY)
    trainer = BlockingTrainer()
    job = TrainingJob(store, trainer)

    job.trigger()
    assert trainer.started.wait(2.0)
    # The trainer already has its snapshot of examples() and is blocked
    # inside the trainer call itself, not inside the store's lock - a read
    # here must return immediately, not wait for the training thread.
    assert store.total_examples() == 0

    trainer.release.set()
    job.close()


def test_build_training_job_is_none_without_a_token_path() -> None:
    config = VisionConfig.development()

    assert build_training_job(config) is None


def test_build_training_job_trains_a_real_candidate_end_to_end(token_workspace: Path) -> None:
    token_path = _token_file(token_workspace)
    config = VisionConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.FIXTURE,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path=str(token_workspace / "cs71d.sock"),
        dataset_path=str(token_workspace / "vision.db"),
        daemon_service_token_path=str(token_path),
        minimum_examples_per_class=2,
    )
    seed_store = DatasetStore.open(config.dataset_path)
    for index, (slot, value) in enumerate([(3, 10), (3, 12), (5, 240), (5, 238)]):
        seed_store.record_example(
            operation_id=f"op-{index}",
            slot=slot,
            frame_png=_png(value),
            frame_captured_at=START,
            operation_created_at=START,
        )
    seed_store.close()

    job = build_training_job(config)
    assert job is not None
    try:
        assert job.trigger() is True
        job.close()

        store = DatasetStore.open(config.dataset_path)
        try:
            candidates = store.candidates()
        finally:
            store.close()
        assert len(candidates) == 1
        assert candidates[0].included_classes == (3, 5)
    finally:
        job.close()


class CountingSuggester:
    def __init__(self) -> None:
        self.calls = 0

    def suggest_once(self) -> int:
        self.calls += 1
        return 0


class ExplodingSuggester:
    def suggest_once(self) -> int:
        raise RuntimeError("suggestion exploded")


def test_suggestion_loop_ticks_repeatedly() -> None:
    suggester = CountingSuggester()
    store = DatasetStore.open(IN_MEMORY)
    loop = SuggestionLoop(suggester, store, interval_ms=1)

    loop.start()
    try:
        deadline = threading.Event()
        deadline.wait(0.05)
    finally:
        loop.close()

    assert suggester.calls >= 1


def test_suggestion_loop_survives_a_tick_that_raises() -> None:
    store = DatasetStore.open(IN_MEMORY)
    loop = SuggestionLoop(ExplodingSuggester(), store, interval_ms=1)

    loop.start()
    deadline = threading.Event()
    deadline.wait(0.05)
    loop.close()  # must not raise or hang


def test_suggestion_loop_closes_its_store() -> None:
    suggester = CountingSuggester()
    store = DatasetStore.open(IN_MEMORY)
    loop = SuggestionLoop(suggester, store, interval_ms=1000)

    loop.start()
    loop.close()

    with pytest.raises(Exception, match="closed"):
        store.total_examples()


def test_suggestion_loop_refuses_a_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        SuggestionLoop(CountingSuggester(), DatasetStore.open(IN_MEMORY), interval_ms=0)


def test_build_suggestion_loop_is_none_without_a_token_path() -> None:
    config = VisionConfig.development()

    assert build_suggestion_loop(config, FrameBuffer()) is None


def test_build_suggestion_loop_builds_a_working_loop_given_a_token_path(
    token_workspace: Path,
) -> None:
    token_path = _token_file(token_workspace)
    config = VisionConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.FIXTURE,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path=str(token_workspace / "cs71d.sock"),
        dataset_path=IN_MEMORY,
        daemon_service_token_path=str(token_path),
    )

    loop = build_suggestion_loop(config, FrameBuffer())

    assert loop is not None
    assert isinstance(loop, SuggestionLoop)
    loop.close()


def test_suggestion_loop_records_a_real_suggestion_end_to_end(token_workspace: Path) -> None:
    db_path = token_workspace / "vision.db"
    store = DatasetStore.open(db_path)
    examples = [
        *[DatasetExample(slot=3, frame_png=_png(10)) for _ in range(10)],
        *[DatasetExample(slot=5, frame_png=_png(240)) for _ in range(10)],
    ]
    candidate = train_candidate(examples, minimum_examples_per_class=5, seed=0)
    version = store.record_candidate(candidate, trained_at=START)
    store.activate(version, activated_at=START)
    store.close()

    reopened = DatasetStore.open(db_path)
    buffer = FrameBuffer()
    buffer.add(Frame(png=_png(10), width=4, height=4, captured_at=START))
    suggester = FrameSuggester(reopened, buffer, now=lambda: START)
    loop = SuggestionLoop(suggester, reopened, interval_ms=1)

    loop.start()
    try:
        deadline = threading.Event()
        for _ in range(200):
            check = DatasetStore.open(db_path)
            try:
                latest = check.latest_suggestion()
            finally:
                check.close()
            if latest is not None:
                assert latest.suggested_slot == 3
                break
            deadline.wait(0.01)
        else:
            raise AssertionError("no suggestion was ever recorded")
    finally:
        loop.close()


class CountingAutonomist:
    def __init__(self) -> None:
        self.calls = 0

    def attempt_once(self) -> int:
        self.calls += 1
        return 0


class ExplodingAutonomist:
    def attempt_once(self) -> int:
        raise RuntimeError("autonomy tick exploded")


def test_autonomy_loop_ticks_repeatedly() -> None:
    autonomist = CountingAutonomist()
    store = DatasetStore.open(IN_MEMORY)
    loop = AutonomyLoop(autonomist, store, interval_ms=1)

    loop.start()
    try:
        deadline = threading.Event()
        deadline.wait(0.05)
    finally:
        loop.close()

    assert autonomist.calls >= 1


def test_autonomy_loop_survives_a_tick_that_raises() -> None:
    store = DatasetStore.open(IN_MEMORY)
    loop = AutonomyLoop(ExplodingAutonomist(), store, interval_ms=1)

    loop.start()
    deadline = threading.Event()
    deadline.wait(0.05)
    loop.close()  # must not raise or hang


def test_autonomy_loop_closes_its_store() -> None:
    autonomist = CountingAutonomist()
    store = DatasetStore.open(IN_MEMORY)
    loop = AutonomyLoop(autonomist, store, interval_ms=1000)

    loop.start()
    loop.close()

    with pytest.raises(Exception, match="closed"):
        store.total_examples()


def test_autonomy_loop_refuses_a_nonpositive_interval() -> None:
    with pytest.raises(ValueError, match="positive"):
        AutonomyLoop(CountingAutonomist(), DatasetStore.open(IN_MEMORY), interval_ms=0)


def test_build_autonomy_loop_is_none_without_a_daemon_token_path() -> None:
    config = VisionConfig.development()

    assert build_autonomy_loop(config) is None


def test_build_autonomy_loop_is_none_without_a_machine_token_path(
    token_workspace: Path,
) -> None:
    token_path = _token_file(token_workspace)
    config = VisionConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.FIXTURE,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path=str(token_workspace / "cs71d.sock"),
        dataset_path=IN_MEMORY,
        daemon_service_token_path=str(token_path),
        # machine_service_token_path left unset: the capability may be
        # provisioned long before it is ever configured (PI-VISION-007/008).
    )

    assert build_autonomy_loop(config) is None


def test_build_autonomy_loop_builds_a_working_loop_given_both_token_paths(
    token_workspace: Path,
) -> None:
    token_path = _token_file(token_workspace)
    machine_token_path = _token_file(token_workspace, name="machine-service-token")
    config = VisionConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.FIXTURE,
        device_path=None,
        capture_interval_ms=1000,
        frame_width=4,
        frame_height=4,
        daemon_socket_path=str(token_workspace / "cs71d.sock"),
        dataset_path=IN_MEMORY,
        daemon_service_token_path=str(token_path),
        machine_service_token_path=str(machine_token_path),
        autonomy_thresholds={3: 0.95},
    )

    loop = build_autonomy_loop(config)

    assert loop is not None
    assert isinstance(loop, AutonomyLoop)
    loop.close()


class _FakeDaemonHandler(BaseHTTPRequestHandler):
    server: _FakeDaemon

    def log_message(self, *args: object) -> None:  # quiet the test output
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server's own naming convention
        if self.path == "/v1/snapshot":
            self._respond(200, {"api_version": "v1", "generation": 41})
            return
        self._respond(404, {"code": "RESOURCE_NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802 - http.server's own naming convention
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length) if length else b""
        if self.path != "/v1/operations/sort":
            self._respond(404, {"code": "RESOURCE_NOT_FOUND"})
            return
        presented = self.headers.get("Authorization", "")
        if presented != f"Bearer {self.server.require_machine_token}":
            self._respond(401, {"code": "UNAUTHENTICATED"})
            return
        payload = json.loads(raw_body)
        self.server.sort_requests.append(payload)
        self._respond(
            202,
            {
                "api_version": "v1",
                "operation_id": f"op-{len(self.server.sort_requests)}",
                "state": "ACCEPTED",
                "generation": 42,
            },
        )

    def _respond(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _FakeDaemon(socketserver.ThreadingUnixStreamServer):
    """A minimal fake `cs71d`: a real snapshot generation, and a
    machine-credential-gated sort route.
    """

    daemon_threads = True
    allow_reuse_address = True
    require_machine_token: str
    sort_requests: list[dict[str, Any]]

    def __init__(self, socket_path: Path) -> None:
        super().__init__(str(socket_path), _FakeDaemonHandler)
        self.sort_requests = []
        self._socket_path = str(socket_path)
        self._thread: threading.Thread | None = None

    @property
    def socket_path(self) -> str:
        return self._socket_path

    def start(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.shutdown()
        self.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def test_autonomy_loop_submits_a_real_autonomous_sort_end_to_end(
    token_workspace: Path,
) -> None:
    """`Autonomist` wired to a real `DaemonClient` against a fake `cs71d`."""
    db_path = token_workspace / "vision.db"
    store = DatasetStore.open(db_path)
    version = store.record_candidate(
        TrainedCandidate(
            model_blob=b"unused-by-this-test",
            included_classes=(3,),
            excluded_classes=(),
            accuracy_by_class={3: 1.0},
            minimum_examples_per_class=1,
            training_example_count=1,
            holdout_example_count=0,
        ),
        trained_at=START,
    )
    store.record_suggestion(
        model_version=version,
        suggested_slot=3,
        confidence=0.97,
        primer_present=False,
        frame_captured_at=START,
        suggested_at=START,
    )

    server = _FakeDaemon(token_workspace / "cs71d.sock")
    server.start()
    try:
        client = DaemonClient(
            server.socket_path, "bff-token", machine_service_token="machine-token"
        )
        server.require_machine_token = "machine-token"
        autonomist = Autonomist(store, client, {3: 0.95}, now=lambda: START)
        loop = AutonomyLoop(autonomist, store, interval_ms=1)

        loop.start()
        try:
            deadline = threading.Event()
            for _ in range(200):
                if server.sort_requests:
                    break
                deadline.wait(0.01)
            else:
                raise AssertionError("no autonomous sort was ever submitted")
        finally:
            loop.close()

        assert server.sort_requests[0]["slot"] == 3
        assert server.sort_requests[0]["actor"] == {"user_id": "cs71-vision", "role": "machine"}
    finally:
        server.close()

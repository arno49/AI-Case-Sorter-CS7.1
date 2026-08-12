from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cs71vision.api import DatasetApiServer
from cs71vision.camera import CameraError, Frame
from cs71vision.config import Backend, ConfigError, Profile, VisionConfig
from cs71vision.correlator import FrameBuffer
from cs71vision.dataset import IN_MEMORY, DatasetStore
from cs71vision.runtime import (
    CaptureLoop,
    CorrelationLoop,
    build_api_server,
    build_camera,
    build_correlation_loop,
    read_service_token,
)

START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


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


def _token_file(directory: Path, *, mode: int = 0o600, content: str = "a-real-token") -> Path:
    path = directory / "service-token"
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
    assert isinstance(server, DatasetApiServer)
    server.close()

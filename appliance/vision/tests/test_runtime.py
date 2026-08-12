from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from cs71vision.camera import CameraError, Frame
from cs71vision.config import Backend, Profile, VisionConfig
from cs71vision.runtime import CaptureLoop, build_camera

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
    )

    with pytest.raises(ValueError, match="device_path"):
        build_camera(config)

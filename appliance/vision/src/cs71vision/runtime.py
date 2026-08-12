"""Assemble and run the vision capture loop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from .camera import Camera, CameraError, FixtureCamera, Frame, V4L2Camera
from .config import Backend, VisionConfig

_LOGGER = logging.getLogger("cs71vision.runtime")

#: What a captured frame is handed to. Wired to the dataset store in
#: PI-VISION-002; here it defaults to a logging no-op so this service is
#: runnable and testable before that store exists.
Sink = Callable[[Frame], None]


def log_sink(frame: Frame) -> None:
    _LOGGER.info(
        "captured a %dx%d frame (%d bytes) at %s",
        frame.width,
        frame.height,
        len(frame.png),
        frame.captured_at.isoformat(),
    )


class CaptureLoop:
    """Poll one `Camera` on a fixed interval and hand each frame to a sink.

    Runs on its own thread so construction/start/close follows the same
    ownership shape `cs71d.runtime.Daemon` already uses: build the pieces,
    start what needs a thread, and close releases exactly what start opened.
    """

    def __init__(
        self,
        camera: Camera,
        *,
        interval_ms: int,
        sink: Sink = log_sink,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self._camera = camera
        self._interval = interval_ms / 1000.0
        self._sink = sink
        self._now = now
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cs71vision-capture", daemon=True)
        self._thread.start()

    def close(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        self._camera.close()

    def __enter__(self) -> CaptureLoop:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                frame = self._camera.read()
            except CameraError:
                _LOGGER.exception("cs71-vision could not capture a frame")
            else:
                try:
                    self._sink(frame)
                except Exception:  # noqa: BLE001 - a sink failure must not kill capture
                    _LOGGER.exception("cs71-vision's frame sink raised")
            self._stop.wait(self._interval)


def build_camera(config: VisionConfig) -> Camera:
    """Turn configuration into the camera the capture loop reads from."""
    if config.backend is Backend.FIXTURE:
        return FixtureCamera(width=config.frame_width, height=config.frame_height)
    if config.device_path is None:
        raise ValueError("v4l2 backend requires device_path")
    return V4L2Camera(config.device_path, width=config.frame_width, height=config.frame_height)

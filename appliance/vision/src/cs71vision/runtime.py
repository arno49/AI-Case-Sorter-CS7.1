"""Assemble and run the vision capture and correlation loops."""

from __future__ import annotations

import logging
import stat
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .camera import Camera, CameraError, FixtureCamera, Frame, V4L2Camera
from .config import Backend, ConfigError, VisionConfig
from .correlator import Correlator, FrameBuffer
from .daemon_client import DaemonClient
from .dataset import DatasetStore

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


#: How often the correlation loop asks cs71d for newly succeeded sorts.
#: Independent of the capture interval - correlation reconciles against
#: cs71d's own durable record, it does not drive capture.
_DEFAULT_POLL_INTERVAL_MS = 2_000


class Poller(Protocol):
    """What the correlation loop needs - `Correlator` today, a fake in tests."""

    def poll_once(self) -> int: ...


class CorrelationLoop:
    """Call `Correlator.poll_once` on a fixed interval, on its own thread.

    Same shape as `CaptureLoop` deliberately: two independent loops, two
    independent intervals, one dataset store between them.
    """

    def __init__(
        self,
        correlator: Poller,
        store: DatasetStore,
        *,
        interval_ms: int = _DEFAULT_POLL_INTERVAL_MS,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self._correlator = correlator
        self._store = store
        self._interval = interval_ms / 1000.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="cs71vision-correlate", daemon=True)
        self._thread.start()

    def close(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        self._store.close()

    def __enter__(self) -> CorrelationLoop:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._correlator.poll_once()
            except Exception:  # noqa: BLE001 - one bad poll must not kill the loop
                _LOGGER.exception("cs71-vision's correlation poll raised")
            self._stop.wait(self._interval)


def read_service_token(path: str | Path) -> str:
    """Read cs71-vision's own copy of the shared service credential.

    Identical contract to `cs71d.runtime.read_service_token`: never a
    configuration value or CLI argument (both are readable through the
    process table by any local user), and refused if reachable by others.
    """
    token_path = Path(path)
    try:
        mode = token_path.stat().st_mode
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"cannot read the service token at {token_path}: {exc}") from exc
    if stat.S_IMODE(mode) & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
        raise ConfigError(f"the service token at {token_path} is readable by other users")
    if not token:
        raise ConfigError(f"the service token at {token_path} is empty")
    return token


def build_correlation_loop(config: VisionConfig, buffer: FrameBuffer) -> CorrelationLoop | None:
    """Build the correlation loop, or None if this config never talks to cs71d.

    `daemon_service_token_path` unset means "capture-only" (PI-VISION-001's
    original mode): no dataset store, no daemon connection, nothing this
    function needs to build.
    """
    if config.daemon_service_token_path is None:
        return None
    token = read_service_token(config.daemon_service_token_path)
    client = DaemonClient(config.daemon_socket_path, token)
    store = DatasetStore.open(config.dataset_path)
    correlator = Correlator(client, store, buffer)
    return CorrelationLoop(correlator, store)

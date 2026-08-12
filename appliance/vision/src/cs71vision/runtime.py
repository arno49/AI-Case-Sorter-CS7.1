"""Assemble and run the vision capture, correlation and training loops."""

from __future__ import annotations

import functools
import logging
import stat
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .api import VisionApiServer
from .camera import Camera, CameraError, FixtureCamera, Frame, V4L2Camera
from .classifier import FrameSuggester
from .config import Backend, ConfigError, VisionConfig
from .correlator import Correlator, FrameBuffer
from .daemon_client import DaemonClient
from .dataset import DatasetExample, DatasetStore, TrainedCandidate
from .training import TrainingError, train_candidate

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


#: How often the suggestion loop re-classifies the latest captured frame.
#: Independent of both the capture and correlation intervals - a fresh
#: suggestion does not need to be recomputed on every new frame, only often
#: enough that an operator looking at the dashboard sees a current one.
_DEFAULT_SUGGESTION_INTERVAL_MS = 2_000


class Suggester(Protocol):
    """What the suggestion loop needs - `FrameSuggester` today, a fake in tests."""

    def suggest_once(self) -> int: ...


class SuggestionLoop:
    """Call `FrameSuggester.suggest_once` on a fixed interval, on its own thread.

    Same shape as `CorrelationLoop` deliberately. Never issues a `cs71d`
    command, under any configuration (ADR-0013, PI-VISION-006's own backlog
    entry) - this loop only ever writes a suggestion row to `vision.db`; the
    operator still submits the sort themselves through the existing
    dashboard form.
    """

    def __init__(
        self,
        suggester: Suggester,
        store: DatasetStore,
        *,
        interval_ms: int = _DEFAULT_SUGGESTION_INTERVAL_MS,
    ) -> None:
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")
        self._suggester = suggester
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
        self._thread = threading.Thread(target=self._run, name="cs71vision-suggest", daemon=True)
        self._thread.start()

    def close(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        self._store.close()

    def __enter__(self) -> SuggestionLoop:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._suggester.suggest_once()
            except Exception:  # noqa: BLE001 - one bad tick must not kill the loop
                _LOGGER.exception("cs71-vision's suggestion tick raised")
            self._stop.wait(self._interval)


def build_suggestion_loop(config: VisionConfig, buffer: FrameBuffer) -> SuggestionLoop | None:
    """Build the suggestion loop, or None if this config never talks to cs71d.

    Same gate every other `vision.db`-backed component uses: without a
    token, correlation never runs, so no example is ever recorded and no
    model is ever trained - there is structurally never an active model
    this loop could suggest from.
    """
    if config.daemon_service_token_path is None:
        return None
    store = DatasetStore.open(config.dataset_path)
    suggester = FrameSuggester(store, buffer)
    return SuggestionLoop(suggester, store)


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


#: What the training job calls to produce a candidate from the dataset's
#: current examples: `cs71vision.training.train_candidate`, with its
#: `minimum_examples_per_class` already bound, in production - a fake in
#: tests, the same shape `Sink`/`Poller` already use for this module's other
#: background loops.
Trainer = Callable[[Sequence[DatasetExample]], TrainedCandidate]


class TrainingJob:
    """Train one candidate on its own thread when triggered; never blocks the caller.

    Deliberately trigger-based, not scheduled: PI-VISION-005 is what exposes
    an operator-facing trigger (the `vision.train` capability) and decides
    when to call `trigger()`. This class only guarantees the call itself
    never blocks, and that two triggers in flight at once collapse into one
    run rather than stacking a second training pass behind the first.
    """

    def __init__(
        self,
        store: DatasetStore,
        trainer: Trainer,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._trainer = trainer
        self._now = now
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def trigger(self) -> bool:
        """Start a training run in the background.

        Returns False, starting nothing, if a run is already in flight: the
        dataset has not changed since that run started reading it, so a
        second concurrent pass would train on the same examples for no
        benefit while doubling the CPU load PI-VISION-004's own "never
        blocks ordinary sorting" requirement is about avoiding.
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            thread = threading.Thread(target=self._run, name="cs71vision-train", daemon=True)
            self._thread = thread
            thread.start()
            return True

    def close(self, *, timeout: float = 30.0) -> None:
        """Wait for an in-flight run to finish, if any, then release the store."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._store.close()

    def _run(self) -> None:
        try:
            examples = self._store.examples()
            candidate = self._trainer(examples)
        except TrainingError:
            _LOGGER.info("training run produced no candidate: not enough classes cleared the floor")
            return
        except Exception:  # noqa: BLE001 - one bad run must not kill this thread
            _LOGGER.exception("cs71-vision's training run failed")
            return
        try:
            self._store.record_candidate(candidate, trained_at=self._now())
        except Exception:  # noqa: BLE001 - a storage failure must not raise off-thread
            _LOGGER.exception("cs71-vision could not record a freshly trained candidate")


def build_training_job(config: VisionConfig) -> TrainingJob | None:
    """Build the training job, or None if this config never talks to cs71d.

    Same gate `build_correlation_loop`/`build_api_server` use, for a
    different reason that lands on the same answer: without a service token,
    `vision.db` never receives an example in the first place because
    correlation itself never runs, so there is structurally nothing this job
    could ever train from.
    """
    if config.daemon_service_token_path is None:
        return None
    store = DatasetStore.open(config.dataset_path)
    trainer: Trainer = functools.partial(
        train_candidate, minimum_examples_per_class=config.minimum_examples_per_class
    )
    return TrainingJob(store, trainer)


def build_api_server(config: VisionConfig) -> VisionApiServer | None:
    """Build the vision API server, or None if this config never talks to cs71d.

    Same gate `build_correlation_loop`/`build_training_job` use: without a
    service token there is no credential to authenticate a caller against
    and no dataset ever being populated, so capture-only mode exposes no API
    either.

    Opens its own `DatasetStore` handle, independent of the one
    `build_correlation_loop` opens, and its own `TrainingJob` (with its own
    independent `DatasetStore` handle in turn) rather than sharing either -
    see `VisionApiServer`'s own docstring for why that is safe and
    deliberate. `VisionApiServer.close()` closes both.
    """
    if config.daemon_service_token_path is None:
        return None
    token = read_service_token(config.daemon_service_token_path)
    store = DatasetStore.open(config.dataset_path)
    training_job = build_training_job(config)
    assert training_job is not None  # same gate already checked above
    return VisionApiServer(
        store,
        socket_path=config.api_socket_path,
        service_token=token,
        minimum_examples_per_class=config.minimum_examples_per_class,
        training_job=training_job,
    )

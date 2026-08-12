"""Camera capture: one `Frame` value and two implementations of `Camera`.

`FixtureCamera` is deterministic and needs no hardware -- the same evidence
role `appliance/daemon/src/cs71d/simulator` plays for the serial protocol.
It is what this workspace's own tests and CI actually exercise.

`V4L2Camera` opens a real Video4Linux2 device through OpenCV
(`cv2.VideoCapture(..., cv2.CAP_V4L2)`) rather than a hand-rolled ioctl
implementation. That trade-off is deliberate, not a default: this process
would otherwise need to reconstruct the V4L2 mmap-streaming protocol
(`v4l2_format`, `v4l2_requestbuffers`, `v4l2_buffer` and their exact
kernel-ABI struct layouts) from memory, with no real V4L2 device anywhere in
this development or CI environment to catch a wrong field offset against.
Every small, permissively-licensed "V4L2 Python bindings" package on PyPI
that exists to avoid that is itself GPL-licensed (a direct ctypes
transcription of the GPL `linux/videodev2.h` kernel header), which does not
fit this workspace's MIT license. OpenCV is Apache-2.0 (the wrapper
packaging is MIT), battle-tested against exactly this class of UVC/V4L2
webcam, and removes both the correctness risk and the licensing question in
one move -- worth the dependency weight for code that talks to hardware
this environment cannot test against at all.

Frames are PNG-encoded in both implementations, not raw sensor bytes, so
anything downstream (dataset storage, training, inference) works
identically against a fixture or a real camera.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import cv2
import numpy as np


class CameraError(RuntimeError):
    """A camera could not be opened, configured or read."""


@dataclass(frozen=True, slots=True)
class Frame:
    """One captured frame, PNG-encoded."""

    png: bytes
    width: int
    height: int
    captured_at: datetime


class Camera(Protocol):
    """A source of frames. `cs71vision.runtime.CaptureLoop` only needs this."""

    def read(self) -> Frame:
        """Capture and return the next frame, or raise `CameraError`."""

    def close(self) -> None:
        """Release the underlying device. Idempotent."""


class FixtureCamera:
    """A deterministic, dependency-free camera for development and tests.

    Each frame is a solid-color image whose value advances with an internal
    counter, never with wall-clock time or randomness -- "the frame changed"
    is exercisable the same way every time this runs.
    """

    def __init__(
        self,
        *,
        width: int,
        height: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if width <= 0 or height <= 0:
            raise CameraError("fixture camera width and height must be positive")
        self._width = width
        self._height = height
        self._now = now
        self._frame_count = 0
        self._closed = False

    def read(self) -> Frame:
        if self._closed:
            raise CameraError("fixture camera is closed")
        self._frame_count += 1
        value = self._frame_count % 256
        image = np.full((self._height, self._width, 3), value, dtype=np.uint8)
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise CameraError("fixture camera failed to encode a synthetic frame")
        return Frame(
            png=encoded.tobytes(),
            width=self._width,
            height=self._height,
            captured_at=self._now(),
        )

    def close(self) -> None:
        self._closed = True


class V4L2Camera:
    """A real camera reached through OpenCV's V4L2 backend.

    Opening happens eagerly in `__init__`, on the caller's thread, the same
    convention `cs71d.device.create_transport_factory`'s returned factory
    uses for the serial transport: construction is a single, attributable
    step a caller can catch `CameraError` around, not something hidden
    inside the first `read()`.
    """

    def __init__(
        self,
        device_path: str,
        *,
        width: int,
        height: int,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._device_path = device_path
        self._now = now
        capture = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
        if not capture.isOpened():
            capture.release()
            raise CameraError(f"cannot open camera device {device_path}")
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._capture = capture

    def read(self) -> Frame:
        ok, image = self._capture.read()
        if not ok:
            raise CameraError(f"failed to read a frame from {self._device_path}")
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            raise CameraError(f"failed to encode a frame from {self._device_path}")
        height, width = image.shape[0], image.shape[1]
        return Frame(png=encoded.tobytes(), width=width, height=height, captured_at=self._now())

    def close(self) -> None:
        self._capture.release()

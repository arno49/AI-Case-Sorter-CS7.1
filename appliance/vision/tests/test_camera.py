from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cs71vision.camera import CameraError, FixtureCamera

START = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


def test_a_fixture_camera_produces_a_decodable_png() -> None:
    import cv2
    import numpy as np

    camera = FixtureCamera(width=8, height=6, now=lambda: START)

    frame = camera.read()

    assert frame.width == 8
    assert frame.height == 6
    assert frame.captured_at == START
    decoded = cv2.imdecode(np.frombuffer(frame.png, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None
    assert decoded.shape == (6, 8, 3)


def test_a_fixture_camera_changes_content_every_frame() -> None:
    camera = FixtureCamera(width=4, height=4, now=lambda: START)

    first = camera.read()
    second = camera.read()

    assert first.png != second.png


def test_a_fixture_camera_is_deterministic_given_the_same_frame_count() -> None:
    left = FixtureCamera(width=4, height=4, now=lambda: START)
    right = FixtureCamera(width=4, height=4, now=lambda: START)

    assert left.read().png == right.read().png
    assert left.read().png == right.read().png


def test_a_fixture_camera_refuses_a_nonpositive_size() -> None:
    with pytest.raises(CameraError, match="positive"):
        FixtureCamera(width=0, height=4)
    with pytest.raises(CameraError, match="positive"):
        FixtureCamera(width=4, height=-1)


def test_a_closed_fixture_camera_refuses_to_read() -> None:
    camera = FixtureCamera(width=4, height=4)
    camera.close()

    with pytest.raises(CameraError, match="closed"):
        camera.read()


def test_closing_a_fixture_camera_is_idempotent() -> None:
    camera = FixtureCamera(width=4, height=4)

    camera.close()
    camera.close()  # must not raise


def test_v4l2_camera_refuses_a_device_it_cannot_open() -> None:
    from cs71vision.camera import V4L2Camera

    with pytest.raises(CameraError, match="cannot open"):
        V4L2Camera("/dev/does-not-exist-cs71-vision-test", width=8, height=6)

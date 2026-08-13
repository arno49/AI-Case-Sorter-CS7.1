from __future__ import annotations

import cv2
import numpy as np
import pytest

from cs71vision.classifier import ClassificationError, classify_frame
from cs71vision.dataset import DatasetExample
from cs71vision.training import train_candidate


def _png(value: int, *, size: int = 8) -> bytes:
    image = np.full((size, size, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _trained_model_blob() -> bytes:
    examples = [
        *(DatasetExample(slot=3, frame_png=_png(10)) for _ in range(20)),
        *(DatasetExample(slot=5, frame_png=_png(240)) for _ in range(20)),
    ]
    candidate = train_candidate(examples, minimum_examples_per_class=5, seed=0)
    return candidate.model_blob


def test_classify_frame_picks_the_matching_class_with_high_confidence() -> None:
    model_blob = _trained_model_blob()

    suggestion = classify_frame(model_blob, _png(10))

    assert suggestion.slot == 3
    assert suggestion.confidence > 0.5


def test_classify_frame_confidence_is_a_probability() -> None:
    model_blob = _trained_model_blob()

    suggestion = classify_frame(model_blob, _png(240))

    assert 0.0 <= suggestion.confidence <= 1.0
    assert suggestion.slot == 5


def test_classify_frame_refuses_a_malformed_model_blob() -> None:
    with pytest.raises(ClassificationError, match="could not load"):
        classify_frame(b"not-a-pickled-model", _png(10))


def test_classify_frame_refuses_an_undecodable_frame() -> None:
    model_blob = _trained_model_blob()

    with pytest.raises(ClassificationError, match="could not decode"):
        classify_frame(model_blob, b"not-a-png")

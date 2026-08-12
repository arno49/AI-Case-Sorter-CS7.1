from __future__ import annotations

import cv2
import numpy as np
import pytest

from cs71vision.dataset import DatasetExample
from cs71vision.training import (
    FEATURE_SIZE,
    FeatureError,
    TrainingError,
    extract_features,
    train_candidate,
)


def _png(value: int, *, size: int = 8) -> bytes:
    """A solid-color square image - trivially separable by intensity alone."""
    image = np.full((size, size, 3), value, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def _examples(slot: int, count: int, *, value: int) -> list[DatasetExample]:
    return [DatasetExample(slot=slot, frame_png=_png(value)) for _ in range(count)]


class TestExtractFeatures:
    def test_returns_a_fixed_length_vector_regardless_of_source_resolution(self) -> None:
        small = extract_features(_png(10, size=4))
        large = extract_features(_png(10, size=64))

        assert small.shape == (FEATURE_SIZE * FEATURE_SIZE,)
        assert large.shape == (FEATURE_SIZE * FEATURE_SIZE,)

    def test_normalizes_intensity_to_the_unit_interval(self) -> None:
        features = extract_features(_png(255))

        assert features.max() <= 1.0
        assert features.min() >= 0.0

    def test_is_deterministic_for_the_same_frame(self) -> None:
        frame = _png(42)

        assert np.array_equal(extract_features(frame), extract_features(frame))

    def test_refuses_a_frame_that_is_not_a_decodable_image(self) -> None:
        with pytest.raises(FeatureError, match="could not decode"):
            extract_features(b"not-a-png")


class TestTrainCandidate:
    def test_excludes_a_class_below_the_floor_outright(self) -> None:
        examples = [
            *_examples(3, 10, value=10),
            *_examples(5, 10, value=200),
            *_examples(7, 2, value=100),  # below the floor of 5
        ]

        candidate = train_candidate(examples, minimum_examples_per_class=5, seed=0)

        assert candidate.included_classes == (3, 5)
        assert candidate.excluded_classes == (7,)
        # The excluded class's examples never reached feature extraction or
        # training at all, not merely omitted from the summary.
        assert candidate.training_example_count + candidate.holdout_example_count == 20

    def test_raises_when_fewer_than_two_classes_clear_the_floor(self) -> None:
        examples = _examples(3, 10, value=10)

        with pytest.raises(TrainingError, match="at least two classes"):
            train_candidate(examples, minimum_examples_per_class=5, seed=0)

    def test_raises_on_an_entirely_empty_dataset(self) -> None:
        with pytest.raises(TrainingError, match="at least two classes"):
            train_candidate([], minimum_examples_per_class=5, seed=0)

    def test_computes_per_class_holdout_accuracy_for_a_trivially_separable_dataset(self) -> None:
        examples = [*_examples(3, 20, value=10), *_examples(5, 20, value=240)]

        candidate = train_candidate(examples, minimum_examples_per_class=5, seed=0)

        assert candidate.accuracy_by_class[3] == pytest.approx(1.0)
        assert candidate.accuracy_by_class[5] == pytest.approx(1.0)

    def test_holdout_examples_are_never_also_used_for_training(self) -> None:
        examples = [*_examples(3, 10, value=10), *_examples(5, 10, value=240)]

        candidate = train_candidate(
            examples, minimum_examples_per_class=5, holdout_fraction=0.5, seed=0
        )

        assert candidate.training_example_count == 10
        assert candidate.holdout_example_count == 10

    def test_a_single_example_class_trains_but_is_skipped_for_holdout_evaluation(self) -> None:
        examples = [*_examples(3, 1, value=10), *_examples(5, 10, value=240)]

        candidate = train_candidate(examples, minimum_examples_per_class=1, seed=0)

        assert candidate.included_classes == (3, 5)
        assert 3 not in candidate.accuracy_by_class
        assert 5 in candidate.accuracy_by_class

    def test_is_deterministic_given_the_same_seed(self) -> None:
        examples = [*_examples(3, 20, value=10), *_examples(5, 20, value=240)]

        first = train_candidate(examples, minimum_examples_per_class=5, seed=7)
        second = train_candidate(examples, minimum_examples_per_class=5, seed=7)

        assert first.model_blob == second.model_blob
        assert first.accuracy_by_class == second.accuracy_by_class

    def test_records_the_floor_that_was_actually_applied(self) -> None:
        examples = [*_examples(3, 10, value=10), *_examples(5, 10, value=240)]

        candidate = train_candidate(examples, minimum_examples_per_class=7, seed=0)

        assert candidate.minimum_examples_per_class == 7

    def test_refuses_an_out_of_range_holdout_fraction(self) -> None:
        examples = [*_examples(3, 10, value=10), *_examples(5, 10, value=240)]

        with pytest.raises(ValueError, match="holdout_fraction"):
            train_candidate(examples, minimum_examples_per_class=5, holdout_fraction=1.0)
        with pytest.raises(ValueError, match="holdout_fraction"):
            train_candidate(examples, minimum_examples_per_class=5, holdout_fraction=0.0)

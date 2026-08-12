"""Turn captured frames into a feature vector, and a labeled dataset into a
trained candidate classifier (PI-VISION-004).

Phase 0 (ADR-0013): no model and no dataset existed before PI-VISION-002, so
this is the first model this workspace ever trains. Feature extraction is
deliberately simple - a small, fixed-size grayscale pixel vector, no learned
embedding and no external model file to fetch - so a first candidate is
reproducible from nothing but this repository and the examples PI-VISION-002
already records. `scikit-learn` (BSD-3-Clause) is the classifier: the same
permissive-license, well-tested-over-hand-rolled reasoning that put OpenCV
over a hand-rolled V4L2 implementation in PI-VISION-001, and a minimal
footprint appropriate for a background job training on tens to a few hundred
examples per class on a Pi 5 CPU - not a deep-learning framework sized for a
workload this is not.
"""

from __future__ import annotations

import pickle
import random
from collections.abc import Sequence

import cv2
import numpy as np
from sklearn.ensemble import RandomForestClassifier

from .dataset import DatasetExample, TrainedCandidate

#: Every frame is resized to this square before feature extraction,
#: regardless of the capture resolution a config used - so every example
#: yields the same vector length no matter what produced the frame.
FEATURE_SIZE = 32

_RANDOM_FOREST_TREES = 100
_DEFAULT_HOLDOUT_FRACTION = 0.2


class TrainingError(RuntimeError):
    """A candidate could not be trained from what the dataset currently holds."""


class FeatureError(RuntimeError):
    """A captured frame could not be turned into a feature vector."""


def extract_features(frame_png: bytes) -> np.ndarray:
    """A fixed-length, deterministic feature vector for one captured frame.

    Decodes to grayscale, resizes to `FEATURE_SIZE` x `FEATURE_SIZE`, and
    normalizes pixel intensity to [0, 1]. No randomness and no learned
    embedding - this is a bootstrap feature set, not a claim that it is the
    best one. PI-VISION-004's acceptance evidence is the pipeline and its
    measured, recorded accuracy, not this choice being optimal; a better
    feature set is free to replace this function later without touching
    anything downstream of it.
    """
    array = np.frombuffer(frame_png, dtype=np.uint8)
    decoded = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if decoded is None:
        raise FeatureError("could not decode a captured frame as an image")
    resized = cv2.resize(decoded, (FEATURE_SIZE, FEATURE_SIZE), interpolation=cv2.INTER_AREA)
    return (resized.astype(np.float64) / 255.0).flatten()


def train_candidate(
    examples: Sequence[DatasetExample],
    *,
    minimum_examples_per_class: int,
    holdout_fraction: float = _DEFAULT_HOLDOUT_FRACTION,
    seed: int = 0,
) -> TrainedCandidate:
    """Train one candidate classifier, enforcing the per-class floor.

    A class below `minimum_examples_per_class` is excluded from the training
    set outright (ADR-0013) - not merely flagged - because a class with a
    handful of examples would let this function produce a confidently wrong
    model with no signal about why. Held-out accuracy is computed per
    included class from a deterministic split (`seed`, a `random.Random`
    instance local to this call - never wall-clock or global random state),
    strictly from examples the classifier never trained on.

    Raises `TrainingError` when fewer than two classes clear the floor: a
    classifier needs at least two classes to mean anything, and "train a
    one-class model" is not a candidate PI-VISION-005 should ever be able to
    offer for activation.
    """
    if not (0.0 < holdout_fraction < 1.0):
        raise ValueError("holdout_fraction must be between 0 and 1")

    by_slot: dict[int, list[DatasetExample]] = {}
    for example in examples:
        by_slot.setdefault(example.slot, []).append(example)

    included_slots = sorted(
        slot for slot, items in by_slot.items() if len(items) >= minimum_examples_per_class
    )
    excluded_slots = sorted(slot for slot in by_slot if slot not in included_slots)
    if len(included_slots) < 2:
        raise TrainingError(
            "at least two classes must clear the minimum-example floor to train a"
            f" classifier; {len(included_slots)} did"
        )

    rng = random.Random(seed)
    train_features: list[np.ndarray] = []
    train_labels: list[int] = []
    holdout_by_slot: dict[int, tuple[list[np.ndarray], list[int]]] = {}
    for slot in included_slots:
        items = list(by_slot[slot])
        rng.shuffle(items)
        # At least one example always stays in the training pool; a class
        # with exactly one example (an installer-configured floor of 1)
        # trains on it and is simply skipped for held-out evaluation below,
        # rather than this function refusing to train at all.
        holdout_count = max(1, round(len(items) * holdout_fraction)) if len(items) > 1 else 0
        holdout_count = min(holdout_count, len(items) - 1)
        holdout_items, train_items = items[:holdout_count], items[holdout_count:]

        for item in train_items:
            train_features.append(extract_features(item.frame_png))
            train_labels.append(slot)
        if holdout_items:
            holdout_by_slot[slot] = (
                [extract_features(item.frame_png) for item in holdout_items],
                [slot for _ in holdout_items],
            )

    classifier = RandomForestClassifier(n_estimators=_RANDOM_FOREST_TREES, random_state=seed)
    classifier.fit(np.stack(train_features), np.array(train_labels))

    accuracy_by_class: dict[int, float] = {}
    holdout_example_count = 0
    for slot, (features, labels) in holdout_by_slot.items():
        predictions = classifier.predict(np.stack(features))
        correct = int(np.sum(predictions == np.array(labels)))
        accuracy_by_class[slot] = correct / len(labels)
        holdout_example_count += len(labels)

    return TrainedCandidate(
        model_blob=pickle.dumps(classifier),
        included_classes=tuple(included_slots),
        excluded_classes=tuple(excluded_slots),
        accuracy_by_class=accuracy_by_class,
        minimum_examples_per_class=minimum_examples_per_class,
        training_example_count=len(train_labels),
        holdout_example_count=holdout_example_count,
    )

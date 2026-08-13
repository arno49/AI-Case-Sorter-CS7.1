"""Turn a stored model and one captured frame into a suggested class and
confidence (PI-VISION-006).

Read-only classification: this module never issues a `cs71d` command, under
any configuration (ADR-0013, this task's own backlog entry). It only ever
answers "what would this model say" - the operator still submits the sort
themselves through the existing dashboard form, and their actual choice,
whether or not they follow the suggestion, keeps feeding PI-VISION-002's
dataset loop unchanged. Autonomous submission needs a new daemon actor kind
that does not exist yet (PI-VISION-007) and a confidence-gated policy on
top of it (PI-VISION-008); nothing here reaches toward either.

`classify_frame`'s own `slot` is a manufacturer-class label, not
necessarily a physical chute - `FrameSuggester` passes it through
`cs71vision.routing.RoutingSession.route` before it is ever stored or
suggested (PI-VISION-009). When no routing run is active that call is the
identity function, so this is exactly PI-VISION-006's original behaviour
unless an operator has explicitly started one.
"""

from __future__ import annotations

import logging
import pickle
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from .correlator import FrameBuffer
from .dataset import DatasetError, DatasetStore
from .training import FeatureError, extract_features

_LOGGER = logging.getLogger("cs71vision.classifier")


class ClassificationError(RuntimeError):
    """A frame could not be classified against a stored model."""


@dataclass(frozen=True, slots=True)
class Suggestion:
    slot: int
    confidence: float
    #: The primer-presence axis (PI-VISION-010, ADR-0013/SAF-09), permanently
    #: separate from `slot`/`confidence`. `None` means unknown/ambiguous, not
    #: "no primer" - see `cs71vision.primer` for why this is always `None`
    #: today and what that means for `requires_operator_confirmation`.
    primer_present: bool | None


def classify_frame(model_blob: bytes, frame_png: bytes) -> Suggestion:
    """The model's own top class and confidence for one captured frame.

    `model_blob` is never anything but a `pickle.dumps` output this same
    workspace produced (`training.train_candidate`, stored by
    `DatasetStore.record_candidate`) - the same trust boundary
    `training.py` already accepts for the same reason, not a new one this
    module introduces.
    """
    try:
        classifier = pickle.loads(model_blob)  # noqa: S301 - see docstring: this process's own output
    except Exception as exc:
        raise ClassificationError(f"could not load the stored model: {exc}") from exc
    try:
        features = extract_features(frame_png)
    except FeatureError as exc:
        raise ClassificationError(str(exc)) from exc

    probabilities = classifier.predict_proba(features.reshape(1, -1))[0]
    best_index = int(probabilities.argmax())
    slot = int(classifier.classes_[best_index])
    confidence = float(probabilities[best_index])
    # No trained primer detector exists yet (cs71vision.primer) - this is
    # never anything but None until real primer-labeled training data and a
    # validated model for it exist, neither of which this task builds.
    return Suggestion(slot=slot, confidence=confidence, primer_present=None)


class Router(Protocol):
    """What the suggester needs to turn a class into a chute - `RoutingSession` today."""

    def route(self, class_id: int) -> int: ...


class _IdentityRouter:
    """The default when no `RoutingSession` is given: unrouted, as before PI-VISION-009."""

    def route(self, class_id: int) -> int:
        return class_id


_IDENTITY_ROUTER = _IdentityRouter()


class FrameSuggester:
    """Classify the latest captured frame against the active model, once.

    The one thing `runtime.SuggestionLoop` calls each tick - kept separate
    from the loop's own threading/timing concerns, the same way
    `correlator.Correlator` is kept separate from `runtime.CorrelationLoop`.
    """

    def __init__(
        self,
        store: DatasetStore,
        buffer: FrameBuffer,
        *,
        router: Router = _IDENTITY_ROUTER,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._buffer = buffer
        self._router = router
        self._now = now

    def suggest_once(self) -> int:
        """Return 1 if a suggestion was recorded, 0 otherwise.

        0 covers every reason nothing was recorded - no model active yet,
        nothing captured yet, or the active model/frame could not be used -
        the caller does not need to distinguish them.
        """
        active_version = self._store.active_version()
        if active_version is None:
            return 0
        frame = self._buffer.latest()
        if frame is None:
            return 0
        try:
            model_blob = self._store.candidate_model(active_version)
        except DatasetError:
            _LOGGER.exception("cs71-vision could not load the active model to suggest with")
            return 0
        try:
            suggestion = classify_frame(model_blob, frame.png)
        except ClassificationError:
            _LOGGER.exception("cs71-vision could not classify the latest frame")
            return 0
        self._store.record_suggestion(
            model_version=active_version,
            suggested_slot=self._router.route(suggestion.slot),
            confidence=suggestion.confidence,
            primer_present=suggestion.primer_present,
            frame_captured_at=frame.captured_at,
            suggested_at=self._now(),
        )
        return 1

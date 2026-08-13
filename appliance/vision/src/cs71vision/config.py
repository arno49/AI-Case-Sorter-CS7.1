"""Strict `cs71-vision` configuration with fail-safe profile defaults.

Mirrors `cs71d.config` deliberately: the same three profiles, the same
"production fixes every path so there is nothing left to misconfigure"
posture, the same refusal of unknown fields. A camera source is exactly as
much a hardware boundary as a serial port, so it gets the same discipline.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when vision configuration violates an appliance invariant."""


class Profile(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Backend(StrEnum):
    FIXTURE = "fixture"
    V4L2 = "v4l2"


#: The udev-created stable symlink for the approved camera module, the same
#: shape `cs71d.config.PRODUCTION_DEVICE_PATH` is for the serial adapter.
PRODUCTION_DEVICE_PATH = "/dev/cs71vision"
#: Same socket cs71-web already reads from - cs71d has exactly one.
PRODUCTION_DAEMON_SOCKET_PATH = "/run/cs71/cs71d.sock"
#: cs71-vision's own copy of the shared service credential, the same
#: two-file (now three) pattern PI-OPS-001 established for cs71d/cs71-web.
PRODUCTION_DAEMON_SERVICE_TOKEN_PATH = "/etc/cs71-vision/service-token"
#: cs71-vision's own copy of the *distinct* machine credential PI-VISION-007
#: provisions to only cs71d and cs71-vision, never cs71-web
#: (`write_machine_service_token`, `appliance/ops/lib/common.sh`). Presenting
#: it is the only way `submit_sort` can ever act (PI-VISION-008).
PRODUCTION_MACHINE_SERVICE_TOKEN_PATH = "/etc/cs71-vision/machine-service-token"
#: The self-labeled dataset store (PI-VISION-002); cs71-vision exclusively
#: owns it, the same separate-ownership rule machine.db/web.db follow.
PRODUCTION_DATASET_PATH = "/var/lib/cs71-vision/vision.db"
#: cs71-vision's own read-only dataset API (PI-VISION-003), the socket
#: cs71-web reads from - cs71-vision has exactly one, the same shape
#: PRODUCTION_DAEMON_SOCKET_PATH is for cs71d.
PRODUCTION_API_SOCKET_PATH = "/run/cs71-vision/cs71vision.sock"
#: Development/test default matching cs71d's and cs71-web's own dev socket.
_DEVELOPMENT_DAEMON_SOCKET_PATH = "/tmp/cs71/cs71d.sock"
_DEVELOPMENT_DATASET_PATH = "/tmp/cs71-vision/vision.db"
_DEVELOPMENT_API_SOCKET_PATH = "/tmp/cs71-vision/cs71vision.sock"

_ALLOWED_FIELDS = {
    "profile",
    "backend",
    "device_path",
    "capture_interval_ms",
    "frame_width",
    "frame_height",
    "daemon_socket_path",
    "daemon_service_token_path",
    "dataset_path",
    "api_socket_path",
    "minimum_examples_per_class",
    "machine_service_token_path",
    "autonomy_thresholds",
}

#: A per-class confidence threshold outside this range could never mean
#: anything: 0.0 would make every class trivially eligible (a threshold, not
#: a real judgment call), and `predict_proba` never exceeds 1.0.
_MINIMUM_THRESHOLD = 0.0
_MAXIMUM_THRESHOLD = 1.0

_DEFAULT_INTERVAL_MS = 1_000
#: The CameraV2 USB VGA module's native resolution
#: (3DModels/Classifier/CameraV2/Camera Assembly V2.pdf).
_DEFAULT_WIDTH = 640
_DEFAULT_HEIGHT = 480
#: A hard floor, not a suggestion (ADR-0013): a class below this is excluded
#: from training and the UI says why, rather than training on a class too
#: thin to trust. No number is specified by the ADR; this is a conservative
#: starting default and is configurable per installation.
_DEFAULT_MINIMUM_EXAMPLES_PER_CLASS = 40


@dataclass(frozen=True, slots=True)
class VisionConfig:
    profile: Profile
    backend: Backend
    device_path: str | None
    capture_interval_ms: int
    frame_width: int
    frame_height: int
    daemon_socket_path: str
    dataset_path: str
    #: None means "do not correlate a dataset" - PI-VISION-001's plain
    #: capture-only mode still works without ever reaching cs71d. The dataset
    #: API (PI-VISION-003) shares this gate: with no dataset ever populated
    #: and no credential to authenticate a caller against, it has nothing to
    #: serve either.
    daemon_service_token_path: str | None = None
    api_socket_path: str = _DEVELOPMENT_API_SOCKET_PATH
    minimum_examples_per_class: int = _DEFAULT_MINIMUM_EXAMPLES_PER_CLASS
    #: None means "the machine role is unreachable" - the same structural
    #: gate `cs71d.config.DaemonConfig.machine_service_token_path` uses
    #: (PI-VISION-007): this capability can be provisioned long before it is
    #: ever used. Optional even in production.
    machine_service_token_path: str | None = None
    #: Per-class autonomous-sort confidence threshold (PI-VISION-008,
    #: ADR-0013). A class absent here can never autonomously sort - the
    #: empty default is the conservative "mostly manual" starting point the
    #: ADR calls for, not a placeholder to fill in later.
    autonomy_thresholds: Mapping[int, float] = field(default_factory=dict)

    @classmethod
    def development(cls) -> VisionConfig:
        return cls(
            profile=Profile.DEVELOPMENT,
            backend=Backend.FIXTURE,
            device_path=None,
            capture_interval_ms=_DEFAULT_INTERVAL_MS,
            frame_width=_DEFAULT_WIDTH,
            frame_height=_DEFAULT_HEIGHT,
            daemon_socket_path=_DEVELOPMENT_DAEMON_SOCKET_PATH,
            dataset_path=_DEVELOPMENT_DATASET_PATH,
            daemon_service_token_path=None,
            api_socket_path=_DEVELOPMENT_API_SOCKET_PATH,
            minimum_examples_per_class=_DEFAULT_MINIMUM_EXAMPLES_PER_CLASS,
            machine_service_token_path=None,
            autonomy_thresholds={},
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> VisionConfig:
        unknown = set(raw) - _ALLOWED_FIELDS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"unknown vision configuration field(s): {names}")

        try:
            profile = Profile(_required_string(raw, "profile"))
            backend = Backend(_required_string(raw, "backend"))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

        device_path = _optional_string(raw, "device_path")
        capture_interval_ms = _positive_int(raw, "capture_interval_ms", _DEFAULT_INTERVAL_MS)
        frame_width = _positive_int(raw, "frame_width", _DEFAULT_WIDTH)
        frame_height = _positive_int(raw, "frame_height", _DEFAULT_HEIGHT)
        daemon_socket_path = raw.get("daemon_socket_path", _DEVELOPMENT_DAEMON_SOCKET_PATH)
        if not isinstance(daemon_socket_path, str) or not daemon_socket_path:
            raise ConfigError("daemon_socket_path must be a non-empty string")
        dataset_path = raw.get("dataset_path", _DEVELOPMENT_DATASET_PATH)
        if not isinstance(dataset_path, str) or not dataset_path:
            raise ConfigError("dataset_path must be a non-empty string")
        daemon_service_token_path = _optional_string(raw, "daemon_service_token_path")
        api_socket_path = raw.get("api_socket_path", _DEVELOPMENT_API_SOCKET_PATH)
        if not isinstance(api_socket_path, str) or not api_socket_path:
            raise ConfigError("api_socket_path must be a non-empty string")
        minimum_examples_per_class = _positive_int(
            raw, "minimum_examples_per_class", _DEFAULT_MINIMUM_EXAMPLES_PER_CLASS
        )
        machine_service_token_path = _optional_string(raw, "machine_service_token_path")
        autonomy_thresholds = _autonomy_thresholds(raw)

        if backend is Backend.FIXTURE and device_path is not None:
            raise ConfigError("fixture backend cannot specify device_path")

        if profile is Profile.PRODUCTION:
            if backend is not Backend.V4L2:
                raise ConfigError("production profile requires v4l2 backend")
            if device_path != PRODUCTION_DEVICE_PATH:
                raise ConfigError(f"production device_path must be {PRODUCTION_DEVICE_PATH}")
            if daemon_socket_path != PRODUCTION_DAEMON_SOCKET_PATH:
                raise ConfigError(
                    f"production daemon_socket_path must be {PRODUCTION_DAEMON_SOCKET_PATH}"
                )
            if dataset_path != PRODUCTION_DATASET_PATH:
                raise ConfigError(f"production dataset_path must be {PRODUCTION_DATASET_PATH}")
            if daemon_service_token_path != PRODUCTION_DAEMON_SERVICE_TOKEN_PATH:
                raise ConfigError(
                    "production daemon_service_token_path must be"
                    f" {PRODUCTION_DAEMON_SERVICE_TOKEN_PATH}"
                )
            if api_socket_path != PRODUCTION_API_SOCKET_PATH:
                raise ConfigError(
                    f"production api_socket_path must be {PRODUCTION_API_SOCKET_PATH}"
                )
            if (
                machine_service_token_path is not None
                and machine_service_token_path != PRODUCTION_MACHINE_SERVICE_TOKEN_PATH
            ):
                raise ConfigError(
                    "production machine_service_token_path must be"
                    f" {PRODUCTION_MACHINE_SERVICE_TOKEN_PATH} when set"
                )
        elif backend is Backend.V4L2:
            raise ConfigError("v4l2 backend is reserved for the production profile")

        return cls(
            profile=profile,
            backend=backend,
            device_path=device_path,
            capture_interval_ms=capture_interval_ms,
            frame_width=frame_width,
            frame_height=frame_height,
            daemon_socket_path=daemon_socket_path,
            dataset_path=dataset_path,
            daemon_service_token_path=daemon_service_token_path,
            api_socket_path=api_socket_path,
            minimum_examples_per_class=minimum_examples_per_class,
            machine_service_token_path=machine_service_token_path,
            autonomy_thresholds=autonomy_thresholds,
        )


def load_config(path: str | Path | None = None) -> VisionConfig:
    if path is None:
        return VisionConfig.development()

    config_path = Path(path)
    try:
        with config_path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot load configuration {config_path}: {exc}") from exc

    if set(document) != {"vision"}:
        raise ConfigError("configuration must contain only a [vision] table")
    vision = document["vision"]
    if not isinstance(vision, dict):
        raise ConfigError("[vision] must be a table")
    return VisionConfig.from_mapping(vision)


#: Mirrors `cs71d.api.MAX_API_SLOT` - a threshold for a slot the daemon
#: could never accept would be dead configuration, never reachable.
_MAX_SLOT = 63


def _autonomy_thresholds(raw: Mapping[str, Any]) -> dict[int, float]:
    entries = raw.get("autonomy_thresholds", [])
    if not isinstance(entries, list):
        raise ConfigError("autonomy_thresholds must be a list of {slot, threshold} tables")
    thresholds: dict[int, float] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"slot", "threshold"}:
            raise ConfigError(
                "each autonomy_thresholds entry must carry exactly slot and threshold"
            )
        slot = entry["slot"]
        if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot <= _MAX_SLOT:
            raise ConfigError(f"autonomy_thresholds slot must be between 0 and {_MAX_SLOT}")
        if slot in thresholds:
            raise ConfigError(f"autonomy_thresholds has more than one entry for slot {slot}")
        threshold = entry["threshold"]
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not _MINIMUM_THRESHOLD < threshold <= _MAXIMUM_THRESHOLD
        ):
            raise ConfigError(
                "autonomy_thresholds threshold must be greater than"
                f" {_MINIMUM_THRESHOLD} and at most {_MAXIMUM_THRESHOLD}"
            )
        thresholds[slot] = float(threshold)
    return thresholds


def _required_string(raw: Mapping[str, Any], name: str) -> str:
    value = raw.get(name)
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _optional_string(raw: Mapping[str, Any], name: str) -> str | None:
    value = raw.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{name} must be a non-empty string when provided")
    return value


def _positive_int(raw: Mapping[str, Any], name: str, default: int) -> int:
    value = raw.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{name} must be a positive integer")
    return value

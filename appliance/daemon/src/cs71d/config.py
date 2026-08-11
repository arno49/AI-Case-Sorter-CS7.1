"""Strict daemon configuration with fail-safe profile defaults."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when daemon configuration violates an appliance invariant."""


class Profile(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class Backend(StrEnum):
    SIMULATOR = "simulator"
    SERIAL = "serial"


PRODUCTION_DEVICE_PATH = "/dev/cs71"
PRODUCTION_SOCKET_PATH = "/run/cs71/cs71d.sock"
PRODUCTION_DATABASE_PATH = "/var/lib/cs71d/machine.db"
PRODUCTION_SERVICE_TOKEN_PATH = "/etc/cs71d/service-token"

_ALLOWED_FIELDS = {
    "profile",
    "backend",
    "device_path",
    "socket_path",
    "database_path",
    "service_token_path",
}


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    profile: Profile
    backend: Backend
    device_path: str | None
    socket_path: str
    database_path: str
    service_token_path: str | None = None

    @classmethod
    def development(cls) -> DaemonConfig:
        return cls(
            profile=Profile.DEVELOPMENT,
            backend=Backend.SIMULATOR,
            device_path=None,
            socket_path="/tmp/cs71/cs71d.sock",
            database_path="/tmp/cs71/machine.db",
            service_token_path=None,
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> DaemonConfig:
        unknown = set(raw) - _ALLOWED_FIELDS
        if unknown:
            names = ", ".join(sorted(unknown))
            raise ConfigError(f"unknown daemon configuration field(s): {names}")

        try:
            profile = Profile(_required_string(raw, "profile"))
            backend = Backend(_required_string(raw, "backend"))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

        device_path = _optional_string(raw, "device_path")
        socket_path = _required_string(raw, "socket_path")
        database_path = _required_string(raw, "database_path")
        service_token_path = _optional_string(raw, "service_token_path")

        if backend is Backend.SIMULATOR and device_path is not None:
            raise ConfigError("simulator backend cannot specify device_path")

        if profile is Profile.PRODUCTION:
            if backend is not Backend.SERIAL:
                raise ConfigError("production profile requires serial backend")
            if device_path != PRODUCTION_DEVICE_PATH:
                raise ConfigError(f"production device_path must be {PRODUCTION_DEVICE_PATH}")
            if socket_path != PRODUCTION_SOCKET_PATH:
                raise ConfigError(f"production socket_path must be {PRODUCTION_SOCKET_PATH}")
            if database_path != PRODUCTION_DATABASE_PATH:
                raise ConfigError(f"production database_path must be {PRODUCTION_DATABASE_PATH}")
            if service_token_path != PRODUCTION_SERVICE_TOKEN_PATH:
                raise ConfigError(
                    f"production service_token_path must be {PRODUCTION_SERVICE_TOKEN_PATH}"
                )
        elif backend is Backend.SERIAL:
            raise ConfigError("serial backend is reserved for the production profile")

        return cls(
            profile=profile,
            backend=backend,
            device_path=device_path,
            socket_path=socket_path,
            database_path=database_path,
            service_token_path=service_token_path,
        )


def load_config(path: str | Path | None = None) -> DaemonConfig:
    if path is None:
        return DaemonConfig.development()

    config_path = Path(path)
    try:
        with config_path.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot load configuration {config_path}: {exc}") from exc

    if set(document) != {"daemon"}:
        raise ConfigError("configuration must contain only a [daemon] table")
    daemon = document["daemon"]
    if not isinstance(daemon, dict):
        raise ConfigError("[daemon] must be a table")
    return DaemonConfig.from_mapping(daemon)


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

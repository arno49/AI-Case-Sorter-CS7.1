from __future__ import annotations

from pathlib import Path

import pytest

from cs71vision.config import (
    PRODUCTION_API_SOCKET_PATH,
    PRODUCTION_DAEMON_SERVICE_TOKEN_PATH,
    PRODUCTION_DAEMON_SOCKET_PATH,
    PRODUCTION_DATASET_PATH,
    PRODUCTION_DEVICE_PATH,
    Backend,
    ConfigError,
    Profile,
    VisionConfig,
    load_config,
)


def test_development_defaults_are_a_fixture_backend() -> None:
    config = VisionConfig.development()

    assert config.profile is Profile.DEVELOPMENT
    assert config.backend is Backend.FIXTURE
    assert config.device_path is None
    assert config.capture_interval_ms > 0
    assert config.frame_width > 0 and config.frame_height > 0
    assert config.daemon_socket_path
    assert config.dataset_path
    assert config.daemon_service_token_path is None
    assert config.api_socket_path
    assert config.minimum_examples_per_class > 0


def test_load_config_with_no_path_returns_development_defaults() -> None:
    assert load_config(None) == VisionConfig.development()


def test_from_mapping_refuses_an_unknown_field() -> None:
    with pytest.raises(ConfigError, match="unknown vision configuration field"):
        VisionConfig.from_mapping(
            {"profile": "development", "backend": "fixture", "unexpected": "x"}
        )


def test_from_mapping_refuses_an_invalid_profile_or_backend() -> None:
    with pytest.raises(ConfigError):
        VisionConfig.from_mapping({"profile": "not-a-profile", "backend": "fixture"})
    with pytest.raises(ConfigError):
        VisionConfig.from_mapping({"profile": "development", "backend": "not-a-backend"})


def test_fixture_backend_cannot_specify_a_device_path() -> None:
    with pytest.raises(ConfigError, match="cannot specify device_path"):
        VisionConfig.from_mapping(
            {"profile": "development", "backend": "fixture", "device_path": "/dev/video0"}
        )


def test_v4l2_backend_is_reserved_for_the_production_profile() -> None:
    with pytest.raises(ConfigError, match="reserved for the production profile"):
        VisionConfig.from_mapping(
            {"profile": "development", "backend": "v4l2", "device_path": "/dev/video0"}
        )


def test_production_requires_v4l2_backend() -> None:
    with pytest.raises(ConfigError, match="requires v4l2 backend"):
        VisionConfig.from_mapping({"profile": "production", "backend": "fixture"})


def test_production_requires_the_fixed_device_path() -> None:
    with pytest.raises(ConfigError, match=f"must be {PRODUCTION_DEVICE_PATH}"):
        VisionConfig.from_mapping(
            {"profile": "production", "backend": "v4l2", "device_path": "/dev/video0"}
        )


def test_production_requires_the_fixed_daemon_socket_path() -> None:
    with pytest.raises(ConfigError, match="daemon_socket_path must be"):
        VisionConfig.from_mapping(
            {
                "profile": "production",
                "backend": "v4l2",
                "device_path": PRODUCTION_DEVICE_PATH,
                "daemon_socket_path": "/tmp/wrong.sock",
                "daemon_service_token_path": PRODUCTION_DAEMON_SERVICE_TOKEN_PATH,
            }
        )


def test_production_requires_the_fixed_dataset_path() -> None:
    with pytest.raises(ConfigError, match="dataset_path must be"):
        VisionConfig.from_mapping(
            {
                "profile": "production",
                "backend": "v4l2",
                "device_path": PRODUCTION_DEVICE_PATH,
                "daemon_socket_path": PRODUCTION_DAEMON_SOCKET_PATH,
                "dataset_path": "/var/lib/wrong/vision.db",
                "daemon_service_token_path": PRODUCTION_DAEMON_SERVICE_TOKEN_PATH,
            }
        )


def test_production_requires_the_fixed_daemon_service_token_path() -> None:
    with pytest.raises(ConfigError, match="daemon_service_token_path must be"):
        VisionConfig.from_mapping(
            {
                "profile": "production",
                "backend": "v4l2",
                "device_path": PRODUCTION_DEVICE_PATH,
                "daemon_socket_path": PRODUCTION_DAEMON_SOCKET_PATH,
                "dataset_path": PRODUCTION_DATASET_PATH,
                "daemon_service_token_path": "/etc/wrong/service-token",
            }
        )


def test_production_requires_the_fixed_api_socket_path() -> None:
    with pytest.raises(ConfigError, match="api_socket_path must be"):
        VisionConfig.from_mapping(
            {
                "profile": "production",
                "backend": "v4l2",
                "device_path": PRODUCTION_DEVICE_PATH,
                "daemon_socket_path": PRODUCTION_DAEMON_SOCKET_PATH,
                "dataset_path": PRODUCTION_DATASET_PATH,
                "daemon_service_token_path": PRODUCTION_DAEMON_SERVICE_TOKEN_PATH,
                "api_socket_path": "/run/wrong/cs71vision.sock",
            }
        )


def test_a_valid_production_configuration_is_accepted() -> None:
    config = VisionConfig.from_mapping(
        {
            "profile": "production",
            "backend": "v4l2",
            "device_path": PRODUCTION_DEVICE_PATH,
            "daemon_socket_path": PRODUCTION_DAEMON_SOCKET_PATH,
            "dataset_path": PRODUCTION_DATASET_PATH,
            "daemon_service_token_path": PRODUCTION_DAEMON_SERVICE_TOKEN_PATH,
            "api_socket_path": PRODUCTION_API_SOCKET_PATH,
            "minimum_examples_per_class": 25,
        }
    )

    assert config.profile is Profile.PRODUCTION
    assert config.device_path == PRODUCTION_DEVICE_PATH
    assert config.daemon_socket_path == PRODUCTION_DAEMON_SOCKET_PATH
    assert config.dataset_path == PRODUCTION_DATASET_PATH
    assert config.daemon_service_token_path == PRODUCTION_DAEMON_SERVICE_TOKEN_PATH
    assert config.api_socket_path == PRODUCTION_API_SOCKET_PATH
    assert config.minimum_examples_per_class == 25


def test_minimum_examples_per_class_must_be_positive() -> None:
    with pytest.raises(ConfigError, match="minimum_examples_per_class"):
        VisionConfig.from_mapping(
            {"profile": "development", "backend": "fixture", "minimum_examples_per_class": 0}
        )


def test_capture_interval_and_frame_size_must_be_positive() -> None:
    with pytest.raises(ConfigError, match="capture_interval_ms"):
        VisionConfig.from_mapping(
            {"profile": "development", "backend": "fixture", "capture_interval_ms": 0}
        )
    with pytest.raises(ConfigError, match="frame_width"):
        VisionConfig.from_mapping(
            {"profile": "development", "backend": "fixture", "frame_width": -1}
        )


def test_load_config_requires_exactly_one_top_level_vision_table(tmp_path: Path) -> None:
    document = tmp_path / "vision.toml"
    document.write_text('[vision]\nprofile = "development"\nbackend = "fixture"\n[other]\n')

    with pytest.raises(ConfigError, match="only a \\[vision\\] table"):
        load_config(document)


def test_load_config_reports_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot load configuration"):
        load_config(tmp_path / "absent.toml")


def test_load_config_reads_a_real_toml_file(tmp_path: Path) -> None:
    document = tmp_path / "vision.toml"
    document.write_text('[vision]\nprofile = "development"\nbackend = "fixture"\n')

    config = load_config(document)

    assert config.profile is Profile.DEVELOPMENT
    assert config.backend is Backend.FIXTURE

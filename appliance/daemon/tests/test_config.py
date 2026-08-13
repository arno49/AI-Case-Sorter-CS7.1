from pathlib import Path

import pytest

from cs71d import Backend, ConfigError, DaemonConfig, Profile, load_config

CONFIG_DIR = Path(__file__).parents[1] / "config"


def test_default_configuration_is_simulator_without_device() -> None:
    config = load_config()

    assert config.profile is Profile.DEVELOPMENT
    assert config.backend is Backend.SIMULATOR
    assert config.device_path is None


def test_development_example_is_safe() -> None:
    config = load_config(CONFIG_DIR / "development.toml")

    assert config == DaemonConfig.development()


def test_production_example_uses_fixed_device_identity() -> None:
    config = load_config(CONFIG_DIR / "production.example.toml")

    assert config.profile is Profile.PRODUCTION
    assert config.backend is Backend.SERIAL
    assert config.device_path == "/dev/cs71"


def test_production_rejects_simulator() -> None:
    with pytest.raises(ConfigError, match="requires serial backend"):
        DaemonConfig.from_mapping(
            {
                "profile": "production",
                "backend": "simulator",
                "socket_path": "/run/cs71/cs71d.sock",
                "database_path": "/var/lib/cs71d/machine.db",
            }
        )


def test_production_rejects_arbitrary_device_path() -> None:
    with pytest.raises(ConfigError, match="must be /dev/cs71"):
        DaemonConfig.from_mapping(
            {
                "profile": "production",
                "backend": "serial",
                "device_path": "/dev/ttyUSB0",
                "socket_path": "/run/cs71/cs71d.sock",
                "database_path": "/var/lib/cs71d/machine.db",
            }
        )


def test_development_rejects_real_serial_backend() -> None:
    with pytest.raises(ConfigError, match="reserved for the production"):
        DaemonConfig.from_mapping(
            {
                "profile": "development",
                "backend": "serial",
                "device_path": "/dev/cs71",
                "socket_path": "/tmp/cs71/cs71d.sock",
                "database_path": "/tmp/cs71/machine.db",
            }
        )


def test_unknown_configuration_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unknown daemon"):
        DaemonConfig.from_mapping(
            {
                "profile": "development",
                "backend": "simulator",
                "socket_path": "/tmp/cs71/cs71d.sock",
                "database_path": "/tmp/cs71/machine.db",
                "raw_protocol_command": "stop",
            }
        )


def test_machine_service_token_path_is_unset_by_default() -> None:
    # PI-VISION-007's own default: the machine actor kind's credential is
    # optional even in production, since an installation may run this
    # daemon capability for a long time before PI-VISION-008 ever
    # configures anything to present it.
    assert DaemonConfig.development().machine_service_token_path is None
    assert load_config(CONFIG_DIR / "production.example.toml").machine_service_token_path is None


def test_production_accepts_the_fixed_machine_service_token_path() -> None:
    config = DaemonConfig.from_mapping(
        {
            "profile": "production",
            "backend": "serial",
            "device_path": "/dev/cs71",
            "socket_path": "/run/cs71/cs71d.sock",
            "database_path": "/var/lib/cs71d/machine.db",
            "service_token_path": "/etc/cs71d/service-token",
            "machine_service_token_path": "/etc/cs71d/machine-service-token",
        }
    )

    assert config.machine_service_token_path == "/etc/cs71d/machine-service-token"


def test_production_rejects_an_arbitrary_machine_service_token_path() -> None:
    with pytest.raises(ConfigError, match="machine_service_token_path must be"):
        DaemonConfig.from_mapping(
            {
                "profile": "production",
                "backend": "serial",
                "device_path": "/dev/cs71",
                "socket_path": "/run/cs71/cs71d.sock",
                "database_path": "/var/lib/cs71d/machine.db",
                "service_token_path": "/etc/cs71d/service-token",
                "machine_service_token_path": "/etc/cs71d/wrong-path",
            }
        )

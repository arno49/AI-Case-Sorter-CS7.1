from __future__ import annotations

import pytest

from cs71d import (
    DTR_GATE_STATUS,
    Backend,
    DaemonConfig,
    DevicePolicyError,
    DtrGateError,
    Profile,
    SimulatorTransport,
    create_transport_factory,
)
from cs71d.config import (
    PRODUCTION_DATABASE_PATH,
    PRODUCTION_DEVICE_PATH,
    PRODUCTION_SOCKET_PATH,
)


def _production_config() -> DaemonConfig:
    return DaemonConfig(
        profile=Profile.PRODUCTION,
        backend=Backend.SERIAL,
        device_path=PRODUCTION_DEVICE_PATH,
        socket_path=PRODUCTION_SOCKET_PATH,
        database_path=PRODUCTION_DATABASE_PATH,
    )


def test_dtr_gate_is_recorded_as_not_executed() -> None:
    assert DTR_GATE_STATUS == "NOT_EXECUTED"


def test_simulator_backend_returns_a_simulator_factory() -> None:
    factory = create_transport_factory(DaemonConfig.development())

    transport = factory()

    assert isinstance(transport, SimulatorTransport)
    transport.close()


@pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd"])
def test_posix_serial_open_is_blocked_by_the_unqualified_dtr_gate(platform: str) -> None:
    with pytest.raises(DtrGateError) as blocked:
        create_transport_factory(_production_config(), platform=platform)

    assert platform in str(blocked.value)
    assert DTR_GATE_STATUS in str(blocked.value)


def test_serial_factory_is_not_invoked_while_being_created() -> None:
    """Construction must happen on the worker thread, not at policy time."""
    factory = create_transport_factory(_production_config(), platform="win32")

    assert callable(factory)


def test_serial_backend_requires_a_device_path() -> None:
    config = DaemonConfig(
        profile=Profile.PRODUCTION,
        backend=Backend.SERIAL,
        device_path=None,
        socket_path=PRODUCTION_SOCKET_PATH,
        database_path=PRODUCTION_DATABASE_PATH,
    )

    with pytest.raises(DevicePolicyError):
        create_transport_factory(config, platform="win32")

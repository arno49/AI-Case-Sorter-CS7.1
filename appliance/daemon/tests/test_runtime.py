from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cs71d.cli import main
from cs71d.config import Backend, ConfigError, DaemonConfig, Profile
from cs71d.runtime import Daemon, durability_monitor_for, read_service_token
from cs71d.storage_health import DiskSpaceMonitor

TOKEN = "installation-local-service-credential"  # noqa: S105 - test fixture, not a secret


@pytest.fixture
def workspace() -> Iterator[Path]:
    # Unix socket paths are length-limited, so keep the directory short.
    directory = Path(tempfile.mkdtemp(prefix="cs71d"))
    yield directory
    shutil.rmtree(directory, ignore_errors=True)


def _token_file(directory: Path, *, mode: int = 0o640, content: str = TOKEN) -> Path:
    path = directory / "service-token"
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _config(directory: Path, *, token_path: Path | None) -> DaemonConfig:
    return DaemonConfig(
        profile=Profile.DEVELOPMENT,
        backend=Backend.SIMULATOR,
        device_path=None,
        socket_path=str(directory / "cs71d.sock"),
        database_path=str(directory / "machine.db"),
        service_token_path=None if token_path is None else str(token_path),
    )


def _get(socket_path: str, path: str, *, token: str | None = TOKEN) -> tuple[int, dict[str, Any]]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5.0)
    connection.connect(socket_path)
    try:
        authorization = f"Authorization: Bearer {token}\r\n" if token else ""
        request = (
            f"GET {path} HTTP/1.1\r\nHost: localhost\r\n{authorization}Connection: close\r\n\r\n"
        )
        connection.sendall(request.encode())
        raw = b""
        while chunk := connection.recv(4096):
            raw += chunk
    finally:
        connection.close()
    head, _, body = raw.partition(b"\r\n\r\n")
    status = int(head.split(b" ")[1])
    return status, json.loads(body) if body else {}


def test_a_service_token_must_exist_and_be_unreadable_by_others(workspace: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        read_service_token(workspace / "absent")

    world_readable = _token_file(workspace, mode=0o644)
    with pytest.raises(ConfigError, match="readable by other users"):
        read_service_token(world_readable)

    empty = _token_file(workspace, content="   ")
    with pytest.raises(ConfigError, match="is empty"):
        read_service_token(empty)

    assert read_service_token(_token_file(workspace)) == TOKEN


def test_serving_requires_a_configured_credential(workspace: Path) -> None:
    with pytest.raises(ConfigError, match="requires service_token_path"):
        Daemon(_config(workspace, token_path=None))


def test_the_daemon_serves_its_socket_and_releases_everything_on_shutdown(
    workspace: Path,
) -> None:
    config = _config(workspace, token_path=_token_file(workspace))
    daemon = Daemon(config)
    daemon.start(timeout=2.0)
    try:
        status, body = _get(config.socket_path, "/v1/health/ready")

        assert status == 200
        assert body["ready"] is True
        assert body["connection_state"] == "READY"
        assert _get(config.socket_path, "/v1/snapshot", token=None)[0] == 401
        assert Path(config.database_path).exists()
    finally:
        daemon.close(timeout=2.0)

    assert not Path(config.socket_path).exists()
    with pytest.raises(OSError):
        _get(config.socket_path, "/v1/health/live")


def test_shutdown_is_requested_rather_than_forced(workspace: Path) -> None:
    config = _config(workspace, token_path=_token_file(workspace))
    daemon = Daemon(config)
    daemon.start(timeout=2.0)
    finished = threading.Event()

    def serve() -> None:
        daemon.serve_forever()
        finished.set()

    thread = threading.Thread(target=serve, name="serve")
    thread.start()
    assert _get(config.socket_path, "/v1/health/live")[0] == 200

    daemon.request_shutdown()

    assert finished.wait(5.0)
    thread.join(2.0)
    assert not Path(config.socket_path).exists()


def test_a_failed_start_releases_what_it_already_opened(workspace: Path) -> None:
    config = _config(workspace, token_path=_token_file(workspace))
    occupied = Daemon(config)
    occupied.start(timeout=2.0)
    try:
        second = Daemon(config)
        # The first daemon owns the socket path; the second must not leave a
        # half-open journal behind when it fails to take it.
        with pytest.raises(OSError, match="already serving"):
            second.start(timeout=2.0)
        with pytest.raises(RuntimeError, match="not running"):
            _ = second.api
    finally:
        occupied.close(timeout=2.0)


def test_check_config_still_validates_without_serving(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check-config"]) == 0

    assert "configuration valid" in capsys.readouterr().out


def test_the_entry_point_requires_a_mode(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main([])

    assert "required" in capsys.readouterr().err


def test_durability_monitor_for_is_none_outside_production(workspace: Path) -> None:
    config = _config(workspace, token_path=None)

    assert durability_monitor_for(config) is None


def test_durability_monitor_for_wires_disk_and_backup_checks_in_production(workspace: Path) -> None:
    config = DaemonConfig(
        profile=Profile.PRODUCTION,
        backend=Backend.SIMULATOR,
        device_path=None,
        socket_path=str(workspace / "cs71d.sock"),
        database_path=str(workspace / "machine.db"),
        service_token_path=None,
    )

    monitor = durability_monitor_for(config)

    assert monitor is not None
    disk_monitor = monitor.checks[0].__self__  # type: ignore[attr-defined]
    assert isinstance(disk_monitor, DiskSpaceMonitor)
    assert disk_monitor.path == workspace


def test_a_production_profile_daemon_blocks_on_its_own_durability_monitor(
    workspace: Path,
) -> None:
    """An end-to-end proof, not just the builder: a production daemon really
    refuses new work once its own durability monitor reports a threat. No
    backup has ever run against this throwaway workspace, so the fixed
    production backup marker is absent and the monitor reports exactly that -
    the same way it would on a freshly imaged, never-backed-up Pi."""
    config = DaemonConfig(
        profile=Profile.PRODUCTION,
        backend=Backend.SIMULATOR,
        device_path=None,
        socket_path=str(workspace / "cs71d.sock"),
        database_path=str(workspace / "machine.db"),
        service_token_path=str(_token_file(workspace)),
    )
    daemon = Daemon(config)
    daemon.start(timeout=2.0)
    try:
        status, body = _get(config.socket_path, "/v1/health/ready")

        assert status == 200
        assert body["ready"] is False
        assert "backup" in body["reason"]
    finally:
        daemon.close(timeout=2.0)


def test_serving_an_invalid_configuration_reports_it(
    workspace: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    document = workspace / "daemon.toml"
    document.write_text(
        "[daemon]\n"
        'profile = "development"\n'
        'backend = "simulator"\n'
        f'socket_path = "{workspace / "cs71d.sock"}"\n'
        f'database_path = "{workspace / "machine.db"}"\n',
        encoding="utf-8",
    )

    assert main(["--serve", str(document)]) == 2
    assert "service_token_path" in capsys.readouterr().err

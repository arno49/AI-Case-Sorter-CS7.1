"""Assemble the running daemon from its parts, in dependency order.

Ownership is explicit and unwinds in reverse: the journal is opened first
because nothing may be admitted that cannot be recorded, then the domain and
its serial worker, and only then the socket that lets anyone ask for work. On
shutdown the socket closes first, so no new request is admitted while the
worker is still finishing the one it holds.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path

from .api import ApiServer
from .config import Backend, ConfigError, DaemonConfig
from .device import create_transport_factory
from .domain import OperationDomain, WorkerObservers
from .journal import Journal
from .serial_worker import SerialWorker

_LOGGER = logging.getLogger("cs71d.runtime")


def read_service_token(path: str | Path) -> str:
    """Read the installation-local service credential from a protected file.

    The credential is never a configuration value or a command-line argument:
    those are readable by any local user through the process table and the
    configuration file. It is refused if the file is reachable by others.
    """
    token_path = Path(path)
    try:
        mode = token_path.stat().st_mode
        token = token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ConfigError(f"cannot read the service token at {token_path}: {exc}") from exc
    if stat.S_IMODE(mode) & (stat.S_IROTH | stat.S_IWOTH | stat.S_IXOTH):
        raise ConfigError(f"the service token at {token_path} is readable by other users")
    if not token:
        raise ConfigError(f"the service token at {token_path} is empty")
    return token


class Daemon:
    """One running `cs71d`: journal, domain, serial worker and socket API."""

    def __init__(self, config: DaemonConfig) -> None:
        if config.service_token_path is None:
            raise ConfigError("serving requires service_token_path in the configuration")
        self._config = config
        self._token = read_service_token(config.service_token_path)
        self._stopped = threading.Event()
        self._journal: Journal | None = None
        self._domain: OperationDomain | None = None
        self._api: ApiServer | None = None

    @property
    def api(self) -> ApiServer:
        if self._api is None:
            raise RuntimeError("the daemon is not running")
        return self._api

    @property
    def domain(self) -> OperationDomain:
        if self._domain is None:
            raise RuntimeError("the daemon is not running")
        return self._domain

    def start(self, *, timeout: float = 5.0) -> None:
        journal = Journal.open(self._config.database_path)
        self._journal = journal
        try:
            transport_factory = create_transport_factory(self._config)

            def worker_factory(observers: WorkerObservers) -> SerialWorker:
                return SerialWorker(
                    transport_factory,
                    session_observer=observers.session,
                    profile_observer=observers.profile,
                )

            domain = OperationDomain(journal, worker_factory)
            self._domain = domain
            domain.start(timeout=timeout)
            api = ApiServer(
                domain,
                journal,
                socket_path=self._config.socket_path,
                service_token=self._token,
            )
            api.start()
            self._api = api
        except Exception:
            self.close()
            raise
        _LOGGER.info(
            "cs71d serving %s backend on %s",
            self._config.backend,
            self._config.socket_path,
        )

    def close(self, *, timeout: float = 5.0) -> None:
        """Stop accepting requests first, then the worker, then the journal."""
        api, domain, journal = self._api, self._domain, self._journal
        self._api, self._domain, self._journal = None, None, None
        if api is not None:
            self._stop("api", api.close)
        if domain is not None:
            self._stop("domain", lambda: domain.close(timeout=timeout))
        if journal is not None:
            self._stop("journal", journal.close)
        self._stopped.set()

    @staticmethod
    def _stop(component: str, stop: Callable[[], None]) -> None:
        try:
            stop()
        except Exception:
            _LOGGER.exception("cs71d could not stop its %s cleanly", component)

    def request_shutdown(self) -> None:
        self._stopped.set()

    def serve_forever(self) -> None:
        """Block until shutdown is requested, then release everything."""
        try:
            self._stopped.wait()
        finally:
            self.close()

    def __enter__(self) -> Daemon:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def prepare_runtime_directories(config: DaemonConfig) -> None:
    """Create the development directories the packaging owns in production.

    Production directories, their ownership and their modes belong to the
    installer and systemd; the daemon must not invent them with whatever umask
    it happens to be started with.
    """
    if config.backend is Backend.SERIAL:
        return
    for path in (config.socket_path, config.database_path):
        os.makedirs(Path(path).parent, mode=0o700, exist_ok=True)

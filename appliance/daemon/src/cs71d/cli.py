"""Package entry point: validate a configuration, or serve the daemon."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from collections.abc import Sequence
from types import FrameType

from .config import ConfigError, load_config
from .runtime import Daemon, prepare_runtime_directories


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cs71d")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check-config",
        nargs="?",
        const="",
        metavar="PATH",
        help="validate PATH, or the safe development defaults when omitted",
    )
    mode.add_argument(
        "--serve",
        nargs="?",
        const="",
        metavar="PATH",
        help="serve the daemon using PATH, or the development defaults when omitted",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config((args.check_config or args.serve) or None)
    except ConfigError as exc:
        print(f"cs71d: invalid configuration: {exc}", file=sys.stderr)
        return 2

    if args.serve is None:
        device = config.device_path if config.device_path is not None else "none"
        print(
            f"configuration valid: profile={config.profile}"
            f" backend={config.backend} device={device}"
        )
        return 0

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    try:
        prepare_runtime_directories(config)
        daemon = Daemon(config)
        daemon.start()
    except ConfigError as exc:
        print(f"cs71d: cannot serve: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - startup failure paths are device-specific
        print(f"cs71d: startup failed: {exc}", file=sys.stderr)
        return 1

    def handle(signal_number: int, _frame: FrameType | None) -> None:
        logging.getLogger("cs71d").info("stopping on signal %s", signal_number)
        daemon.request_shutdown()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, handle)
    daemon.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

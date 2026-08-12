"""Package entry point: validate a configuration, or serve the capture loop."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Sequence
from types import FrameType

from .config import ConfigError, load_config
from .runtime import CaptureLoop, build_camera


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cs71vision")
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
        help="capture using PATH, or the development defaults when omitted",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config((args.check_config or args.serve) or None)
    except ConfigError as exc:
        print(f"cs71vision: invalid configuration: {exc}", file=sys.stderr)
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
        camera = build_camera(config)
    except Exception as exc:  # noqa: BLE001 - startup failure paths are device-specific
        print(f"cs71vision: startup failed: {exc}", file=sys.stderr)
        return 1

    loop = CaptureLoop(camera, interval_ms=config.capture_interval_ms)
    loop.start()

    stopped = threading.Event()

    def handle(signal_number: int, _frame: FrameType | None) -> None:
        logging.getLogger("cs71vision").info("stopping on signal %s", signal_number)
        stopped.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, handle)
    try:
        stopped.wait()
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

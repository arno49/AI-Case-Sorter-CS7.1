"""Package entry point: validate a configuration, or serve the capture loop."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from collections.abc import Sequence
from types import FrameType

from .camera import Frame
from .config import ConfigError, load_config
from .correlator import FrameBuffer
from .runtime import CaptureLoop, build_api_server, build_camera, build_correlation_loop, log_sink


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
    buffer = FrameBuffer()
    try:
        camera = build_camera(config)
        correlation_loop = build_correlation_loop(config, buffer)
        api_server = build_api_server(config)
    except Exception as exc:  # noqa: BLE001 - startup failure paths are device-specific
        print(f"cs71vision: startup failed: {exc}", file=sys.stderr)
        return 1

    def sink(frame: Frame) -> None:
        log_sink(frame)
        buffer.add(frame)

    capture_loop = CaptureLoop(camera, interval_ms=config.capture_interval_ms, sink=sink)
    capture_loop.start()
    if correlation_loop is not None:
        correlation_loop.start()
        logging.getLogger("cs71vision").info("dataset correlation is running against cs71d")
    else:
        logging.getLogger("cs71vision").info(
            "no daemon_service_token_path configured; capturing only, no dataset correlation"
        )
    if api_server is not None:
        api_server.start()
        logging.getLogger("cs71vision").info("dataset api is serving %s", api_server.socket_path)

    stopped = threading.Event()

    def handle(signal_number: int, _frame: FrameType | None) -> None:
        logging.getLogger("cs71vision").info("stopping on signal %s", signal_number)
        stopped.set()

    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, handle)
    try:
        stopped.wait()
    finally:
        capture_loop.close()
        if correlation_loop is not None:
            correlation_loop.close()
        if api_server is not None:
            api_server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

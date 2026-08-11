"""Minimal package entry point for configuration validation."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .config import ConfigError, load_config


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cs71d")
    parser.add_argument(
        "--check-config",
        nargs="?",
        const="",
        metavar="PATH",
        help="validate PATH, or the safe development defaults when omitted",
    )
    args = parser.parse_args(argv)

    if args.check_config is None:
        parser.error("daemon runtime is not implemented; use --check-config")

    try:
        config = load_config(args.check_config or None)
    except ConfigError as exc:
        print(f"cs71d: invalid configuration: {exc}", file=sys.stderr)
        return 2

    device = config.device_path if config.device_path is not None else "none"
    print(f"configuration valid: profile={config.profile} backend={config.backend} device={device}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

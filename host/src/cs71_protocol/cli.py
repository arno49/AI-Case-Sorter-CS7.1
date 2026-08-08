"""The safe CS7.1 v1/v2 compatibility switcher command."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from .client import ProtocolClient
from .errors import DtrSuppressionError, ProtocolError, RecoveryError, TimeoutError
from .locking import PortLock, PortLockError
from .models import SessionMode
from .transport import SerialTransport

EXIT_SUCCESS = 0
EXIT_USAGE = 2
EXIT_PORT_UNAVAILABLE = 3
EXIT_PROTOCOL = 4
EXIT_RECOVERY = 5
EXIT_DTR_GUARANTEE = 6
EXIT_CHILD_LAUNCH = 7


class DtrGuaranteeError(RuntimeError):
    """Opening the port could have reset the controller before a safe stop."""


class SafetyGuaranteeError(RuntimeError):
    """The requested operation cannot establish its required safety barrier."""


class UsageError(ValueError):
    """An argparse usage error rendered by ``main`` in the selected format."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


class Lock(Protocol):
    def acquire(self) -> None: ...
    def release(self) -> None: ...


@dataclass
class Dependencies:
    opener: Callable[..., Any] = SerialTransport.open
    lock_factory: Callable[[str], Lock] = PortLock
    runner: Callable[[Sequence[str]], Any] = lambda argv: subprocess.run(argv, check=False)


def _close(transport: Any) -> None:
    close = getattr(transport, "close", None)
    if callable(close):
        close()


@contextmanager
def _session(port: str, timeout: float, dependencies: Dependencies):
    lock = dependencies.lock_factory(port)
    transport: Any | None = None
    lock.acquire()
    try:
        transport = dependencies.opener(port, baudrate=9600, timeout=min(timeout, 0.05))
        if not getattr(transport, "dtr_suppression_guaranteed", False):
            raise DtrGuaranteeError(
                "could not guarantee DTR suppression while opening the port; no safe stop was sent"
            )
        yield transport
    finally:
        try:
            if transport is not None:
                _close(transport)
        finally:
            lock.release()


def _reset_and_verify_v1(client: ProtocolClient, transport: Any) -> None:
    """Apply the normative stop/reset/Ready/ping barrier."""
    client.out_of_band_stop()
    try:
        transport.reset()
        client.reader.clear()
        client.wait_ready()
        client.v1_ping_barrier()
    except RecoveryError as exc:
        if exc.recovered:
            raise
        raise RecoveryError(
            "reset did not verify a v1 session", recovered=False
        ) from exc
    except Exception as exc:
        raise RecoveryError(
            "reset did not verify a v1 session", recovered=False
        ) from exc


def _detect(client: ProtocolClient, transport: Any) -> dict[str, Any]:
    _reset_and_verify_v1(client, transport)
    client._write_v1("version")
    version = client._read_v1()
    available = client.discover()
    return {
        "v2_available": available,
        "firmware": "v2-capable" if available else "v1-only",
        "mode": "v1",
        "version": version,
        "reset": True,
    }


def _prepare(port: str, timeout: float, dependencies: Dependencies) -> dict[str, Any]:
    with _session(port, timeout, dependencies) as transport:
        client = ProtocolClient(transport, timeout=timeout, reset=transport.reset)
        _reset_and_verify_v1(client, transport)
        return {"mode": "v1", "volatile_settings_reset": True}


def execute(args: argparse.Namespace, dependencies: Dependencies | None = None) -> tuple[int, dict[str, Any]]:
    """Execute a parsed command; injectable dependencies keep tests hardware-free."""
    dependencies = dependencies or Dependencies()
    try:
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            return EXIT_USAGE, {"error": "--timeout must be finite and greater than zero"}
        if args.command in {"detect", "enter-v2"} and getattr(args, "no_reset", False):
            raise SafetyGuaranteeError(
                "--no-reset cannot verify v1 after universal stop; use the required reset barrier"
            )
        if args.command == "prepare-legacy":
            return EXIT_SUCCESS, _prepare(args.port, args.timeout, dependencies)
        if args.command == "run-legacy":
            _prepare(args.port, args.timeout, dependencies)
            application = args.application[1:] if args.application[:1] == ["--"] else []
            if not application:
                return EXIT_USAGE, {"error": "run-legacy requires an application after --"}
            try:
                child = dependencies.runner(application)
            except OSError as exc:
                return EXIT_CHILD_LAUNCH, {"error": str(exc), "prepared": True}
            return int(child.returncode), {"prepared": True, "child_exit": int(child.returncode)}

        with _session(args.port, args.timeout, dependencies) as transport:
            client = ProtocolClient(transport, timeout=args.timeout, reset=transport.reset)
            if args.command == "detect":
                return EXIT_SUCCESS, _detect(client, transport)
            if args.command == "enter-v2":
                result = _detect(client, transport)
                if not result["v2_available"]:
                    return EXIT_SUCCESS, result
                client.activate_known_available()
                if args.crc:
                    client.enable_crc()
                return EXIT_SUCCESS, {
                    **result, "mode": client.mode.value, "crc": client.crc_enabled,
                    "validated": ["protocolversion", "capabilities", "status"],
                }
            if args.command == "leave-v2":
                # A CLI invocation starts without a trustworthy v2 session.  Do
                # not guess framing or CRC: force the universal v1 recovery path.
                _reset_and_verify_v1(client, transport)
                return EXIT_SUCCESS, {"mode": SessionMode.V1.value, "method": "safe-stop-reset-fallback"}
        raise AssertionError("unknown command")
    except (DtrGuaranteeError, DtrSuppressionError) as exc:
        return EXIT_DTR_GUARANTEE, {"error": str(exc), "dtr_suppression_guaranteed": False}
    except SafetyGuaranteeError as exc:
        return EXIT_DTR_GUARANTEE, {"error": str(exc), "safety_guarantee": False}
    except (PortLockError, OSError, RuntimeError) as exc:
        return EXIT_PORT_UNAVAILABLE, {"error": str(exc)}
    except RecoveryError as exc:
        if exc.recovered:
            return EXIT_PROTOCOL, {"error": str(exc), "mode": "v1", "recovered": True}
        return EXIT_RECOVERY, {"error": str(exc), "mode": "uncertain"}
    except (TimeoutError, ProtocolError) as exc:
        return EXIT_PROTOCOL, {"error": str(exc)}


def _add_common(parser: argparse.ArgumentParser, *, no_reset: bool = False) -> None:
    parser.add_argument("--port", required=True, help="serial port (always opened as 9600 8N1)")
    parser.add_argument("--timeout", type=float, default=1.0, help="per-operation timeout in seconds (default: 1)")
    parser.add_argument("--json", action="store_true", dest="command_json", help="write a JSON result to stdout")
    if no_reset:
        parser.add_argument(
            "--no-reset",
            action="store_true",
            help="retained for compatibility; rejected because stop cannot verify v1 without reset",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="cs71-protocol",
        description="Safely switch CS7.1 firmware between v1 and v2.",
    )
    parser.add_argument("--json", action="store_true", dest="global_json", help="write a JSON result to stdout")
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=_ArgumentParser)
    detect = subparsers.add_parser("detect", help="safely detect v2 support without activating it")
    _add_common(detect, no_reset=True)
    enter = subparsers.add_parser("enter-v2", help="detect, activate, and validate protocol v2")
    _add_common(enter, no_reset=True)
    enter.add_argument("--crc", action="store_true", help="enable CRC after successful validation")
    leave = subparsers.add_parser("leave-v2", help="safely reset to verified v1")
    _add_common(leave)
    prepare = subparsers.add_parser("prepare-legacy", help="prepare verified v1 and release the port")
    _add_common(prepare)
    run = subparsers.add_parser("run-legacy", help="prepare v1, release the port, then launch an application")
    _add_common(run)
    run.add_argument("application", nargs=argparse.REMAINDER, help="application and arguments; put after --")
    return parser


def main(argv: Sequence[str] | None = None, *, dependencies: Dependencies | None = None) -> int:
    parser = build_parser()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        args = parser.parse_args(raw_argv)
        if args.command == "run-legacy" and not (
            args.application[:1] == ["--"] and args.application[1:]
        ):
            parser.error("run-legacy requires an application after --")
        if not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error("--timeout must be finite and greater than zero")
    except UsageError as exc:
        if "--json" in raw_argv:
            print(json.dumps({"ok": False, "exit_code": EXIT_USAGE, "error": str(exc)}, sort_keys=True))
        else:
            parser.print_usage(sys.stderr)
            print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    code, result = execute(args, dependencies)
    use_json = bool(getattr(args, "global_json", False) or getattr(args, "command_json", False))
    if use_json:
        print(json.dumps({"ok": code == EXIT_SUCCESS, "exit_code": code, **result}, sort_keys=True))
    elif code == EXIT_SUCCESS:
        print("success: " + ", ".join(f"{key}={value}" for key, value in result.items()))
    elif "child_exit" in result:
        print(f"legacy application exited with status {result['child_exit']}", file=sys.stderr)
    else:
        print(f"error: {result['error']}", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

"""Measure NFR-01 (priority-stop admission latency) and NFR-02 (snapshot
read latency) against a real `cs71d` API server over its own Unix socket -
the same request path a real BFF uses (PI-SWQ-001).

This is software/simulator evidence only, by the same rule this project
applies everywhere else: `docs/architecture/testing-and-quality.md` is
explicit that "simulator or desktop measurements cannot replace this Pi
profile." The harness, the load profile and the report format below are
real and this script actually executes them - what it cannot do on
anything but the exact approved Raspberry Pi 5 hardware is *pass* NFR-01/02
as release evidence. Every report this script writes stamps its own
`evidence_status`, exactly the "NOT_EXECUTED means not performed, not
failed" posture SAF-07 already uses for the Linux DTR gate
(`cs71d.device.DTR_GATE_STATUS`).

Load profile v1 (versioned so a later change to sample counts or
percentile method is a visible, reviewable diff, not a silent redefinition
of what "passing" means):

- Load generator: this script, against `cs71d.api.ApiServer` fronting the
  deterministic simulator (`cs71d.simulator`), never a real controller.
- NFR-01: STOP_SAMPLES priority-stop admissions, each its own idempotency
  key, latency measured from just before the HTTP request is sent to just
  after its response is fully read - the same boundary `docs/architecture/
  api-and-events.md` draws between "admission" and "firmware/physical
  time": the daemon's stop handler returns as soon as the operation is
  durably admitted, before the worker's own result future ever resolves.
- NFR-02: SNAPSHOT_SAMPLES `GET /v1/snapshot` reads, same latency boundary.
- Percentile method: `statistics.quantiles(data, n=100, method="inclusive")`,
  index `PERCENTILE - 1` for the requested percentile - documented here
  because `testing-and-quality.md` requires the method be recorded, not
  just the number.
"""

from __future__ import annotations

import http.client
import json
import platform
import shutil
import socket
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from cs71d.api import ApiServer  # noqa: E402
from cs71d.device import DTR_GATE_STATUS  # noqa: E402
from cs71d.domain import OperationDomain, WorkerObservers  # noqa: E402
from cs71d.events import EventRing  # noqa: E402
from cs71d.journal import IN_MEMORY, Journal  # noqa: E402
from cs71d.serial_worker import SerialWorker  # noqa: E402
from cs71d.simulator import SimulatorConfig, SimulatorTransport  # noqa: E402

LOAD_PROFILE_VERSION = 1
STOP_SAMPLES = 200
SNAPSHOT_SAMPLES = 500
NFR01_THRESHOLD_MS = 250.0
NFR02_THRESHOLD_MS = 100.0
NFR02_PERCENTILE = 99

TOKEN = "measure-nfr-local-credential"  # noqa: S105 - throwaway, this process only


@dataclass(frozen=True, slots=True)
class Sample:
    """One request's admission latency, end to end over the socket."""

    milliseconds: float
    status: int


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str) -> None:
        super().__init__("localhost", timeout=5.0)
        self._socket_path = socket_path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5.0)
        self.sock.connect(self._socket_path)


def _request(socket_path: str, method: str, path: str, *, body: bytes | None = None) -> Sample:
    connection = _UnixConnection(socket_path)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    try:
        started = time.perf_counter()
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        response.read()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return Sample(milliseconds=elapsed_ms, status=response.status)
    finally:
        connection.close()


def _build_server(directory: Path) -> tuple[ApiServer, OperationDomain, Journal]:
    journal = Journal.open(IN_MEMORY, now=lambda: datetime.now(UTC))
    simulators: list[SimulatorTransport] = []

    def open_transport() -> SimulatorTransport:
        simulators.append(SimulatorTransport(SimulatorConfig()))
        return simulators[-1]

    def worker_factory(observers: WorkerObservers) -> SerialWorker:
        return SerialWorker(
            open_transport,
            protocol_timeout=0.1,
            session_observer=observers.session,
            profile_observer=observers.profile,
        )

    domain = OperationDomain(
        journal,
        worker_factory,
        now=lambda: datetime.now(UTC),
        events=EventRing(),
    )
    domain.start(timeout=1.0)
    server = ApiServer(
        domain,
        journal,
        socket_path=directory / "measure-nfr.sock",
        service_token=TOKEN,
        now=lambda: datetime.now(UTC),
    )
    server.start()
    return server, domain, journal


def _measure_stop_admission(socket_path: str) -> list[Sample]:
    samples = []
    for index in range(STOP_SAMPLES):
        body = json.dumps(
            {"api_version": "v1", "actor": {"user_id": "measure-nfr", "role": "operator"}}
        ).encode()
        headers_path = "/v1/machine/stop"
        connection = _UnixConnection(socket_path)
        try:
            started = time.perf_counter()
            connection.request(
                "POST",
                headers_path,
                body=body,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": f"measure-nfr-stop-{index:06d}-{'a' * 16}",
                    "If-Match-Generation": "*",
                    "X-Deadline-Ms": "5000",
                },
            )
            response = connection.getresponse()
            response.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            samples.append(Sample(milliseconds=elapsed_ms, status=response.status))
        finally:
            connection.close()
    return samples


def _measure_snapshot_reads(socket_path: str) -> list[Sample]:
    return [_request(socket_path, "GET", "/v1/snapshot") for _ in range(SNAPSHOT_SAMPLES)]


def _percentile(values: list[float], percentile: int) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=100, method="inclusive")[percentile - 1]


def _is_approved_pi5() -> tuple[bool, str]:
    """Whether this process is actually running on the approved rig.

    Never inferred from `platform.machine() == 'aarch64'` alone - plenty of
    non-Pi ARM hardware exists. The one durable signal this project already
    trusts for hardware identity is the device-tree model string Linux
    exposes on real Pi boards.
    """
    model_path = Path("/proc/device-tree/model")
    if not model_path.exists():
        return False, f"not Linux/device-tree hardware ({platform.platform()})"
    try:
        model = model_path.read_text(encoding="utf-8", errors="replace").strip("\x00").strip()
    except OSError as exc:
        return False, f"could not read {model_path}: {exc}"
    if "Raspberry Pi 5" not in model:
        return False, f"device-tree model is {model!r}, not an approved Raspberry Pi 5"
    return True, model


def _cpu_governor() -> str | None:
    governor_path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if not governor_path.exists():
        return None
    try:
        return governor_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _report(stop_samples: list[Sample], snapshot_samples: list[Sample]) -> dict[str, Any]:
    approved, hardware_detail = _is_approved_pi5()
    stop_admitted = [sample.milliseconds for sample in stop_samples if sample.status == 202]
    snapshot_ok = [sample.milliseconds for sample in snapshot_samples if sample.status == 200]
    stop_p_max = max(stop_admitted) if stop_admitted else None
    snapshot_p99 = _percentile(snapshot_ok, NFR02_PERCENTILE) if snapshot_ok else None
    snapshot_within = (
        sum(1 for value in snapshot_ok if value <= NFR02_THRESHOLD_MS) / len(snapshot_ok)
        if snapshot_ok
        else None
    )

    return {
        "load_profile_version": LOAD_PROFILE_VERSION,
        "evidence_status": "PASS_CANDIDATE" if approved else "NOT_EXECUTED",
        "evidence_note": (
            "Measured on the approved rig; a human reviewer still confirms this before"
            " release, the same as any other qualification result."
            if approved
            else "Measured on non-Pi hardware for harness sanity only. Per"
            " docs/architecture/testing-and-quality.md, simulator/desktop measurements"
            " cannot satisfy NFR-01/NFR-02; re-run this exact script on the approved"
            " Raspberry Pi 5 rig to close this gate."
        ),
        "hardware": {
            "approved_raspberry_pi_5": approved,
            "detail": hardware_detail,
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "cpu_governor": _cpu_governor(),
        },
        "dtr_gate_status": DTR_GATE_STATUS,
        "measured_at": datetime.now(UTC).isoformat(),
        "nfr_01_priority_stop_admission": {
            "requirement_ms": NFR01_THRESHOLD_MS,
            "sample_count": len(stop_samples),
            "admitted_count": len(stop_admitted),
            "max_ms": stop_p_max,
            "within_threshold": (stop_p_max is not None and stop_p_max <= NFR01_THRESHOLD_MS),
            "excludes": "firmware and physical stop time - measured up to the daemon's own"
            " 202 admission response only",
        },
        "nfr_02_snapshot_reads": {
            "requirement_ms": NFR02_THRESHOLD_MS,
            "requirement_percentile": NFR02_PERCENTILE,
            "percentile_method": "statistics.quantiles(n=100, method='inclusive')",
            "sample_count": len(snapshot_samples),
            "ok_count": len(snapshot_ok),
            f"p{NFR02_PERCENTILE}_ms": snapshot_p99,
            "fraction_within_threshold": snapshot_within,
            "within_threshold": (snapshot_within is not None and snapshot_within >= 0.99),
        },
    }


def main() -> int:
    directory = Path(tempfile.mkdtemp(prefix="cs71d-nfr"))
    try:
        server, domain, journal = _build_server(directory)
        try:
            stop_samples = _measure_stop_admission(server.socket_path)
            snapshot_samples = _measure_snapshot_reads(server.socket_path)
        finally:
            server.close()
            domain.close(timeout=1.0)
            journal.close()
    finally:
        shutil.rmtree(directory, ignore_errors=True)

    report = _report(stop_samples, snapshot_samples)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

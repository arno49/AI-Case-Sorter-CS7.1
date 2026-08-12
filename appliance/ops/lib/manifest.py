#!/usr/bin/env python3
"""Build and verify appliance/ops/backup.sh's checksummed manifest.

Kept as portable, dependency-free Python - like appliance/ops/tests/test_artifacts.py
- so this logic is directly unit-testable without root, systemd or a real
install. Three independent jobs:

* ``release-info`` introspects a source checkout once, at install or upgrade
  time, and records what was actually built (commit, daemon/web versions) to
  a small JSON file. Kept separate from ``write`` because a periodic backup
  run from a timer has no live checkout beside it to introspect - only what
  install.sh or upgrade.sh already wrote down when they built the release
  that is actually running.
* ``write`` hashes a backup's files and combines them with that recorded
  release info into one manifest.
* ``verify`` recomputes those hashes against a manifest a restore is about to
  trust, so a corrupted or tampered backup is refused before it overwrites
  anything.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

MANIFEST_FILES = ("machine.db", "web.db", "vision.db", "cs71d.toml", "web.env")
#: vision.db is optional: cs71-vision may not have run yet on an appliance
#: upgraded from a pre-PI-VISION install, and that must not fail a backup or
#: restore that only ever needed machine.db/web.db to work.
REQUIRED_FILES = ("machine.db", "web.db")


class ManifestError(RuntimeError):
    """The manifest is missing, malformed, or does not match its files."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_commit(source_root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = result.stdout.strip()
    return commit if commit else "unknown"


def daemon_version(source_root: Path) -> str:
    path = source_root / "appliance" / "daemon" / "pyproject.toml"
    if not path.is_file():
        return "unknown"
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"))
    return match.group(1) if match else "unknown"


def web_version(source_root: Path) -> str:
    path = source_root / "appliance" / "web" / "package.json"
    if not path.is_file():
        return "unknown"
    document = json.loads(path.read_text(encoding="utf-8"))
    return str(document.get("version", "unknown"))


def build_release_info(source_root: Path, *, installed_at: str) -> dict[str, object]:
    return {
        "installed_at": installed_at,
        "source_commit": source_commit(source_root),
        "daemon_version": daemon_version(source_root),
        "web_version": web_version(source_root),
    }


def load_release_info(release_info_path: Path) -> dict[str, object]:
    if not release_info_path.is_file():
        return {}
    try:
        document = json.loads(release_info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return document if isinstance(document, dict) else {}


def build_manifest(
    backup_dir: Path, release_info_path: Path, created_at: str
) -> dict[str, object]:
    release_info = load_release_info(release_info_path)
    files: dict[str, object] = {}
    for name in MANIFEST_FILES:
        path = backup_dir / name
        if not path.is_file():
            continue
        files[name] = {"sha256": sha256_of(path), "bytes": path.stat().st_size}
    return {
        "created_at": created_at,
        "source_commit": str(release_info.get("source_commit", "unknown")),
        "daemon_version": str(release_info.get("daemon_version", "unknown")),
        "web_version": str(release_info.get("web_version", "unknown")),
        "files": files,
    }


def verify(backup_dir: Path) -> None:
    manifest_path = backup_dir / "manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"no manifest at {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest at {manifest_path} is not valid JSON: {exc}") from exc

    files = document.get("files") if isinstance(document, dict) else None
    if not isinstance(files, dict) or not files:
        raise ManifestError(f"manifest at {manifest_path} lists no files")
    for required in REQUIRED_FILES:
        if required not in files:
            raise ManifestError(f"manifest at {manifest_path} does not include {required}")
    for name, entry in files.items():
        path = backup_dir / name
        if not path.is_file():
            raise ManifestError(f"manifest lists {name}, missing from {backup_dir}")
        expected = entry.get("sha256") if isinstance(entry, dict) else None
        if not isinstance(expected, str):
            raise ManifestError(f"manifest entry for {name} has no sha256")
        actual = sha256_of(path)
        if actual != expected:
            raise ManifestError(
                f"{name} does not match its manifest checksum; the backup may be corrupt"
            )


def _usage() -> int:
    print("usage: manifest.py release-info SOURCE_ROOT OUTPUT_PATH INSTALLED_AT", file=sys.stderr)
    print("       manifest.py write BACKUP_DIR RELEASE_INFO_PATH CREATED_AT", file=sys.stderr)
    print("       manifest.py verify BACKUP_DIR", file=sys.stderr)
    return 2


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return _usage()
    command = argv[1]
    try:
        if command == "release-info" and len(argv) == 5:
            info = build_release_info(Path(argv[2]), installed_at=argv[4])
            Path(argv[3]).write_text(json.dumps(info, indent=2, sort_keys=True), encoding="utf-8")
            return 0
        if command == "write" and len(argv) == 5:
            manifest = build_manifest(Path(argv[2]), Path(argv[3]), argv[4])
            print(json.dumps(manifest, indent=2, sort_keys=True))
            return 0
        if command == "verify" and len(argv) == 3:
            verify(Path(argv[2]))
            print("manifest verified")
            return 0
    except ManifestError as exc:
        print(f"manifest.py: {exc}", file=sys.stderr)
        return 1
    return _usage()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

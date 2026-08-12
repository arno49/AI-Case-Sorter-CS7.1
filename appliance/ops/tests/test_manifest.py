"""Unit and CLI tests for lib/manifest.py: no root, systemd or real install
needed - the same portable, dependency-free style as test_artifacts.py."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

OPS_DIR = Path(__file__).resolve().parents[1]
LIB_DIR = OPS_DIR / "lib"
sys.path.insert(0, str(LIB_DIR))

import manifest  # noqa: E402


def _write(path: Path, content: str = "") -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class BuildReleaseInfo(unittest.TestCase):
    def test_reads_the_daemon_and_web_versions_from_their_own_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "appliance" / "daemon").mkdir(parents=True, exist_ok=True)
            (root / "appliance" / "daemon" / "pyproject.toml").write_text(
                '[project]\nname = "cs71d"\nversion = "0.1.0"\n', encoding="utf-8"
            )
            (root / "appliance" / "web").mkdir(parents=True, exist_ok=True)
            (root / "appliance" / "web" / "package.json").write_text(
                json.dumps({"name": "cs71-web", "version": "0.0.1"}), encoding="utf-8"
            )

            info = manifest.build_release_info(root, installed_at="2026-08-12T00:00:00Z")

            self.assertEqual(info["daemon_version"], "0.1.0")
            self.assertEqual(info["web_version"], "0.0.1")
            self.assertEqual(info["installed_at"], "2026-08-12T00:00:00Z")
            self.assertEqual(info["source_commit"], "unknown")  # not a git checkout

    def test_reads_the_real_commit_from_this_actual_checkout(self) -> None:
        repo_root = OPS_DIR.parents[1]
        info = manifest.build_release_info(repo_root, installed_at="2026-08-12T00:00:00Z")

        self.assertNotEqual(info["source_commit"], "unknown")
        self.assertRegex(info["source_commit"], r"^[0-9a-f]{40}$")


class LoadReleaseInfo(unittest.TestCase):
    def test_missing_file_is_an_empty_mapping(self) -> None:
        self.assertEqual(manifest.load_release_info(Path("/does/not/exist.json")), {})

    def test_malformed_json_is_an_empty_mapping_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = _write(Path(directory) / "release-info.json", "not json")
            self.assertEqual(manifest.load_release_info(path), {})


class BuildManifest(unittest.TestCase):
    def test_hashes_every_present_backup_file_and_carries_the_release_info(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = Path(directory) / "backup"
            backup_dir.mkdir()
            _write(backup_dir / "machine.db", "machine-db-contents")
            _write(backup_dir / "web.db", "web-db-contents")
            release_info = _write(
                Path(directory) / "release-info.json",
                json.dumps(
                    {
                        "source_commit": "deadbeef",
                        "daemon_version": "0.1.0",
                        "web_version": "0.0.1",
                    }
                ),
            )

            built = manifest.build_manifest(backup_dir, release_info, "2026-08-12T00:00:00Z")

            self.assertEqual(built["created_at"], "2026-08-12T00:00:00Z")
            self.assertEqual(built["source_commit"], "deadbeef")
            self.assertEqual(built["daemon_version"], "0.1.0")
            self.assertEqual(built["web_version"], "0.0.1")
            self.assertEqual(set(built["files"]), {"machine.db", "web.db"})
            self.assertEqual(
                built["files"]["machine.db"]["sha256"],
                manifest.sha256_of(backup_dir / "machine.db"),
            )
            # cs71d.toml/web.env are optional: a backup taken before either
            # config existed must not fail to manifest what it does have.
            self.assertNotIn("cs71d.toml", built["files"])

    def test_missing_release_info_falls_back_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = Path(directory) / "backup"
            backup_dir.mkdir()
            _write(backup_dir / "machine.db", "x")
            _write(backup_dir / "web.db", "y")

            built = manifest.build_manifest(
                backup_dir, Path(directory) / "absent.json", "2026-08-12T00:00:00Z"
            )

            self.assertEqual(built["source_commit"], "unknown")
            self.assertEqual(built["daemon_version"], "unknown")


class Verify(unittest.TestCase):
    def _backup(self, directory: Path) -> Path:
        backup_dir = directory / "backup"
        backup_dir.mkdir()
        _write(backup_dir / "machine.db", "machine-db-contents")
        _write(backup_dir / "web.db", "web-db-contents")
        manifest_document = {
            "created_at": "2026-08-12T00:00:00Z",
            "source_commit": "deadbeef",
            "daemon_version": "0.1.0",
            "web_version": "0.0.1",
            "files": {
                "machine.db": {
                    "sha256": manifest.sha256_of(backup_dir / "machine.db"),
                    "bytes": (backup_dir / "machine.db").stat().st_size,
                },
                "web.db": {
                    "sha256": manifest.sha256_of(backup_dir / "web.db"),
                    "bytes": (backup_dir / "web.db").stat().st_size,
                },
            },
        }
        _write(backup_dir / "manifest.json", json.dumps(manifest_document))
        return backup_dir

    def test_a_matching_manifest_verifies_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = self._backup(Path(directory))
            manifest.verify(backup_dir)  # must not raise

    def test_a_tampered_file_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = self._backup(Path(directory))
            (backup_dir / "machine.db").write_text("tampered", encoding="utf-8")

            with self.assertRaisesRegex(manifest.ManifestError, "does not match"):
                manifest.verify(backup_dir)

    def test_a_missing_file_the_manifest_expects_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = self._backup(Path(directory))
            (backup_dir / "web.db").unlink()

            with self.assertRaisesRegex(manifest.ManifestError, "missing from"):
                manifest.verify(backup_dir)

    def test_a_missing_manifest_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = Path(directory) / "backup"
            backup_dir.mkdir()

            with self.assertRaisesRegex(manifest.ManifestError, "no manifest"):
                manifest.verify(backup_dir)

    def test_a_manifest_missing_machine_db_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            backup_dir = self._backup(Path(directory))
            document = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
            del document["files"]["machine.db"]
            _write(backup_dir / "manifest.json", json.dumps(document))

            with self.assertRaisesRegex(manifest.ManifestError, "machine.db"):
                manifest.verify(backup_dir)


class CommandLineInterface(unittest.TestCase):
    """The actual invocation contract backup.sh and restore.sh depend on."""

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(LIB_DIR / "manifest.py"), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_release_info_write_then_manifest_write_then_verify_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "appliance" / "daemon").mkdir(parents=True)
            (root / "appliance" / "daemon" / "pyproject.toml").write_text(
                'version = "0.1.0"\n', encoding="utf-8"
            )
            (root / "appliance" / "web").mkdir(parents=True)
            (root / "appliance" / "web" / "package.json").write_text(
                json.dumps({"version": "0.0.1"}), encoding="utf-8"
            )
            release_info = root / "release-info.json"
            backup_dir = root / "backup"
            backup_dir.mkdir()
            (backup_dir / "machine.db").write_text("m", encoding="utf-8")
            (backup_dir / "web.db").write_text("w", encoding="utf-8")

            written = self._run(
                "release-info", str(root), str(release_info), "2026-08-12T00:00:00Z"
            )
            self.assertEqual(written.returncode, 0, written.stderr)
            self.assertEqual(
                json.loads(release_info.read_text(encoding="utf-8"))["daemon_version"], "0.1.0"
            )

            manifest_out = self._run(
                "write", str(backup_dir), str(release_info), "2026-08-12T01:00:00Z"
            )
            self.assertEqual(manifest_out.returncode, 0, manifest_out.stderr)
            (backup_dir / "manifest.json").write_text(manifest_out.stdout, encoding="utf-8")

            verified = self._run("verify", str(backup_dir))
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertIn("verified", verified.stdout)

    def test_verify_exits_nonzero_and_says_why_on_a_bad_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = self._run("verify", directory)

            self.assertEqual(result.returncode, 1)
            self.assertIn("no manifest", result.stderr)

    def test_an_unknown_command_prints_usage_and_exits_nonzero(self) -> None:
        result = self._run("not-a-command")

        self.assertEqual(result.returncode, 2)
        self.assertIn("usage", result.stderr)


if __name__ == "__main__":
    unittest.main()

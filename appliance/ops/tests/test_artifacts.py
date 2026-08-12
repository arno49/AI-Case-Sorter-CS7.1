"""Structural checks on the installer's own artifacts.

These do not need a Pi, root, or even Linux - they check that the systemd
units, udev rule, Caddyfile and install.sh agree with each other and with the
paths the daemon and web workspaces actually read (appliance/daemon/src/cs71d/config.py,
appliance/web/src/lib/server/config.ts). What they cannot prove is that a real
systemd actually accepts these directives or that the sandboxed processes
still function under them - that is smoke-test.sh's job, and it needs a real
Linux host to run.
"""

import re
import subprocess
import unittest
from pathlib import Path

OPS_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = OPS_DIR.parents[1]

# The paths every artifact must agree on, taken verbatim from the code that
# actually enforces them in production.
PRODUCTION_SOCKET_PATH = "/run/cs71/cs71d.sock"
PRODUCTION_DAEMON_DATABASE_PATH = "/var/lib/cs71d/machine.db"
PRODUCTION_WEB_DATABASE_PATH = "/var/lib/cs71-web/web.db"
PRODUCTION_DEVICE_PATH = "/dev/cs71"
DAEMON_SERVICE_TOKEN_PATH = "/etc/cs71d/service-token"
WEB_SERVICE_TOKEN_PATH = "/etc/cs71-web/service-token"
DAEMON_CONFIG_PATH = "/etc/cs71/cs71d.toml"
BACKUP_MARKER_PATH = "/var/lib/cs71d/backup-status.json"
PRODUCTION_CAMERA_DEVICE_PATH = "/dev/cs71vision"
VISION_CONFIG_PATH = "/etc/cs71/cs71vision.toml"
VISION_SERVICE_TOKEN_PATH = "/etc/cs71-vision/service-token"


def parse_unit(path: Path) -> dict[str, list[str]]:
    """A minimal systemd-unit reader: section -> ordered "Key=Value" lines.

    Real unit files allow a key to repeat within a section (e.g. multiple
    ReadWritePaths= lines); a dict keyed by "Section/Key" with list values
    keeps that intact rather than silently keeping only the last one.
    """
    directives: dict[str, list[str]] = {}
    section = ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        directives.setdefault(f"{section}/{key.strip()}", []).append(value.strip())
    return directives


def directive(units: dict[str, list[str]], section: str, key: str) -> str:
    values = units.get(f"{section}/{key}")
    if not values:
        raise AssertionError(f"no {section}/{key} directive in this unit")
    return values[-1]


def directive_words(units: dict[str, list[str]], section: str, key: str) -> set[str]:
    """The tokens of a space-separated directive (e.g. `After=a.service b.service`),
    flattened across every line it appears on."""
    words: set[str] = set()
    for value in units.get(f"{section}/{key}", []):
        words.update(value.split())
    return words


class SystemdUnitsShareOneDesign(unittest.TestCase):
    """Both services describe the same privilege-separation story."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.daemon = parse_unit(OPS_DIR / "systemd/cs71d.service")
        cls.web = parse_unit(OPS_DIR / "systemd/cs71-web.service")

    def test_each_service_runs_as_its_own_unprivileged_identity(self) -> None:
        self.assertEqual(directive(self.daemon, "Service", "User"), "cs71d")
        self.assertEqual(directive(self.web, "Service", "User"), "cs71-web")

    def test_only_the_daemon_may_touch_the_serial_device(self) -> None:
        self.assertIn(
            f"{PRODUCTION_DEVICE_PATH} rw", self.daemon["Service/DeviceAllow"]
        )
        self.assertNotIn("Service/DeviceAllow", self.web)

    def test_the_socket_group_is_the_only_thing_web_shares_with_the_daemon(self) -> None:
        self.assertEqual(directive(self.daemon, "Service", "Group"), "cs71-api")
        self.assertEqual(directive(self.web, "Service", "SupplementaryGroups"), "cs71-api")
        # The web unit's own primary Group is its own identity, not cs71d's -
        # sharing only the one group named above is the point.
        self.assertEqual(directive(self.web, "Service", "Group"), "cs71-web")

    def test_only_the_daemon_ever_declares_the_device_paths(self) -> None:
        daemon_text = (OPS_DIR / "systemd/cs71d.service").read_text(encoding="utf-8")
        web_text = (OPS_DIR / "systemd/cs71-web.service").read_text(encoding="utf-8")
        self.assertIn(PRODUCTION_DEVICE_PATH, daemon_text)
        self.assertNotIn(PRODUCTION_DEVICE_PATH, web_text)

    def test_neither_service_can_gain_new_privileges(self) -> None:
        self.assertEqual(directive(self.daemon, "Service", "NoNewPrivileges"), "yes")
        self.assertEqual(directive(self.web, "Service", "NoNewPrivileges"), "yes")

    def test_neither_service_carries_ambient_capabilities(self) -> None:
        self.assertEqual(directive(self.daemon, "Service", "CapabilityBoundingSet"), "")
        self.assertEqual(directive(self.web, "Service", "CapabilityBoundingSet"), "")

    def test_only_the_daemon_is_confined_to_unix_only(self) -> None:
        # The daemon has no code path that opens an internet socket at all
        # (appliance/daemon/tests/test_api.py asserts that statically); the
        # sandbox is a second, independent enforcement of the same fact.
        self.assertEqual(directive(self.daemon, "Service", "RestrictAddressFamilies"), "AF_UNIX")
        web_families = directive(self.web, "Service", "RestrictAddressFamilies").split()
        self.assertIn("AF_UNIX", web_families)
        self.assertIn("AF_INET", web_families)

    def test_the_daemon_exec_start_matches_its_installed_venv_and_config(self) -> None:
        exec_start = directive(self.daemon, "Service", "ExecStart")
        self.assertIn("/opt/cs71/daemon/venv/bin/cs71d", exec_start)
        self.assertIn(DAEMON_CONFIG_PATH, exec_start)

    def test_start_limit_directives_are_in_the_unit_section_not_service(self) -> None:
        # systemd silently ignores these two under [Service] rather than
        # refusing the unit - a real, easy-to-miss placement bug that leaves
        # the restart-storm limit not actually configured.
        for unit in (self.daemon, self.web):
            self.assertIn("Unit/StartLimitIntervalSec", unit)
            self.assertIn("Unit/StartLimitBurst", unit)
            self.assertNotIn("Service/StartLimitIntervalSec", unit)
            self.assertNotIn("Service/StartLimitBurst", unit)

    def test_the_web_service_starts_after_the_daemon_but_only_wants_it(self) -> None:
        self.assertIn("cs71d.service", directive_words(self.web, "Unit", "After"))
        self.assertIn("cs71d.service", directive_words(self.web, "Unit", "Wants"))
        # Wants, not Requires: a daemon that is not up must not stop the web
        # service from coming up and saying so (SAF-06).
        self.assertNotIn("Unit/Requires", self.web)

    def test_the_web_service_declares_the_fixed_production_origin_env_file(self) -> None:
        self.assertEqual(directive(self.web, "Service", "EnvironmentFile"), "/etc/cs71/web.env")
        self.assertIn("CS71_WEB_PROFILE=production", self.web["Service/Environment"])


class VisionServiceIsConsistentWithTheInstaller(unittest.TestCase):
    """cs71-vision.service, its udev rule and install.sh's wiring for both."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vision = parse_unit(OPS_DIR / "systemd/cs71-vision.service")
        cls.udev_text = (OPS_DIR / "udev/98-cs71-vision.rules").read_text(encoding="utf-8")
        cls.install_text = (OPS_DIR / "install.sh").read_text(encoding="utf-8")

    def test_runs_as_its_own_unprivileged_identity(self) -> None:
        self.assertEqual(directive(self.vision, "Service", "User"), "cs71-vision")

    def test_has_no_access_to_the_controller_or_either_database(self) -> None:
        # PRODUCTION_DEVICE_PATH ("/dev/cs71") is deliberately not checked
        # with a raw substring test here: it is a prefix of the camera's own
        # "/dev/cs71vision" and would false-fail on that alone.
        allow = self.vision.get("Service/DeviceAllow", [])
        self.assertNotIn(f"{PRODUCTION_DEVICE_PATH} rw", allow)
        text = (OPS_DIR / "systemd/cs71-vision.service").read_text(encoding="utf-8")
        self.assertNotIn(PRODUCTION_DAEMON_DATABASE_PATH, text)
        self.assertNotIn(PRODUCTION_WEB_DATABASE_PATH, text)

    def test_can_only_reach_its_own_camera_device(self) -> None:
        self.assertIn(
            f"{PRODUCTION_CAMERA_DEVICE_PATH} rw", self.vision["Service/DeviceAllow"]
        )

    def test_only_starts_once_its_camera_is_present(self) -> None:
        self.assertEqual(
            directive(self.vision, "Unit", "ConditionPathExists"), PRODUCTION_CAMERA_DEVICE_PATH
        )
        self.assertIn("dev-cs71vision.device", directive_words(self.vision, "Unit", "BindsTo"))

    def test_neither_gains_privileges_nor_carries_capabilities(self) -> None:
        self.assertEqual(directive(self.vision, "Service", "NoNewPrivileges"), "yes")
        self.assertEqual(directive(self.vision, "Service", "CapabilityBoundingSet"), "")

    def test_can_only_reach_cs71d_over_af_unix(self) -> None:
        # Same restriction cs71d.service itself carries; unlike cs71-web,
        # this service has no loopback port of its own, so no AF_INET/AF_INET6.
        self.assertEqual(directive(self.vision, "Service", "RestrictAddressFamilies"), "AF_UNIX")

    def test_reaches_cs71ds_socket_through_the_shared_group_only(self) -> None:
        self.assertEqual(directive(self.vision, "Service", "SupplementaryGroups"), "cs71-api")
        # Its own primary Group is its own identity, not cs71d's - sharing
        # only the one group above is the point.
        self.assertEqual(directive(self.vision, "Service", "Group"), "cs71-vision")

    def test_owns_its_own_dataset_state_directory(self) -> None:
        self.assertEqual(directive(self.vision, "Service", "StateDirectory"), "cs71-vision")

    def test_start_limit_directives_are_in_the_unit_section(self) -> None:
        self.assertIn("Unit/StartLimitIntervalSec", self.vision)
        self.assertIn("Unit/StartLimitBurst", self.vision)
        self.assertNotIn("Service/StartLimitIntervalSec", self.vision)
        self.assertNotIn("Service/StartLimitBurst", self.vision)

    def test_exec_start_matches_its_installed_venv_and_config(self) -> None:
        exec_start = directive(self.vision, "Service", "ExecStart")
        self.assertIn("/opt/cs71/vision/venv/bin/cs71vision", exec_start)
        self.assertIn(VISION_CONFIG_PATH, exec_start)

    def test_udev_rule_matches_video4linux_not_tty(self) -> None:
        self.assertIn('SUBSYSTEM=="video4linux"', self.udev_text)
        self.assertIn('SYMLINK+="cs71vision"', self.udev_text)
        self.assertIn('GROUP="cs71-vision"', self.udev_text)

    def test_udev_rule_matches_no_real_device_until_substituted(self) -> None:
        for placeholder in ("@@CAMERA_VENDOR_ID@@", "@@CAMERA_PRODUCT_ID@@"):
            self.assertIn(placeholder, self.udev_text)

    def test_udev_rule_starts_the_service_the_moment_the_camera_is_present(self) -> None:
        self.assertIn('TAG+="systemd"', self.udev_text)
        self.assertIn('ENV{SYSTEMD_WANTS}="cs71-vision.service"', self.udev_text)

    def test_install_sh_accepts_optional_camera_identity_arguments(self) -> None:
        for flag in ("--camera-vendor-id", "--camera-product-id"):
            self.assertIn(flag, self.install_text)

    def test_install_sh_never_makes_camera_identity_mandatory(self) -> None:
        # The existing mandatory-argument check must still name only the
        # controller's identity; adding the camera there would break every
        # caller that installed before cs71-vision existed, smoke-test.sh
        # included.
        match = re.search(r'\[ -n "\$HOSTNAME_ARG" \].*\|\| usage', self.install_text)
        self.assertIsNotNone(match, "could not find install.sh's mandatory-argument check")
        assert match is not None
        self.assertNotIn("CAMERA_VENDOR_ID", match.group(0))
        self.assertNotIn("CAMERA_PRODUCT_ID", match.group(0))

    def test_install_sh_installs_and_enables_the_vision_service(self) -> None:
        self.assertIn("cs71-vision.service", self.install_text)
        self.assertIn("build_vision_venv", self.install_text)


class VisionConfigAgreesWithTheProductionPath(unittest.TestCase):
    def test_vision_config_module_still_uses_the_paths_this_suite_assumes(self) -> None:
        text = (REPO_ROOT / "appliance/vision/src/cs71vision/config.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(f'"{PRODUCTION_CAMERA_DEVICE_PATH}"', text)
        self.assertIn(f'"{PRODUCTION_SOCKET_PATH}"', text)
        self.assertIn(f'"{VISION_SERVICE_TOKEN_PATH}"', text)

    def test_vision_production_example_toml_matches_the_fixed_paths(self) -> None:
        text = (REPO_ROOT / "appliance/vision/config/production.example.toml").read_text(
            encoding="utf-8"
        )
        for path in (PRODUCTION_CAMERA_DEVICE_PATH, PRODUCTION_SOCKET_PATH, VISION_SERVICE_TOKEN_PATH):
            self.assertIn(path, text)


class CaddyDropInOrdersAfterWeb(unittest.TestCase):
    def test_orders_after_and_wants_the_web_service(self) -> None:
        drop_in = parse_unit(OPS_DIR / "systemd/caddy-cs71.conf")
        self.assertIn("cs71-web.service", drop_in["Unit/After"])
        self.assertIn("cs71-web.service", drop_in["Unit/Wants"])


class UdevRuleMatchesNothingUntilSubstituted(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (OPS_DIR / "udev/99-cs71.rules").read_text(encoding="utf-8")

    def test_creates_the_exact_symlink_the_daemon_config_expects(self) -> None:
        expected_name = PRODUCTION_DEVICE_PATH.removeprefix("/dev/")
        self.assertIn(f'SYMLINK+="{expected_name}"', self.text)

    def test_matches_vendor_product_and_serial_not_vendor_and_product_alone(self) -> None:
        self.assertIn("ATTRS{idVendor}==", self.text)
        self.assertIn("ATTRS{idProduct}==", self.text)
        self.assertIn("ATTRS{serial}==", self.text)

    def test_creates_the_named_symlink_readable_only_by_the_daemon_group(self) -> None:
        self.assertIn('SYMLINK+="cs71"', self.text)
        self.assertIn('GROUP="cs71d"', self.text)
        self.assertIn('MODE="0660"', self.text)

    def test_the_checked_in_rule_matches_no_real_device(self) -> None:
        # No adapter is approved yet (docs/architecture/deployment-and-operations.md);
        # this file must not accidentally ship a real-looking ID.
        for placeholder in ("@@VENDOR_ID@@", "@@PRODUCT_ID@@", "@@SERIAL@@"):
            self.assertIn(placeholder, self.text)

    def test_starts_the_daemon_the_moment_the_adapter_is_present(self) -> None:
        self.assertIn('TAG+="systemd"', self.text)
        self.assertIn('ENV{SYSTEMD_WANTS}="cs71d.service"', self.text)


class CaddyfileExposesOnlyTheWebPort(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (OPS_DIR / "caddy/Caddyfile.cs71").read_text(encoding="utf-8")
        # The prose comments are allowed to name the socket to explain why it
        # is absent; only the config itself must never reach for it.
        self.config_lines = [
            line
            for line in self.text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.config_text = "\n".join(self.config_lines)

    def test_proxies_only_to_loopback(self) -> None:
        self.assertIn("127.0.0.1:@@WEB_PORT@@", self.config_text)
        self.assertNotIn("0.0.0.0", self.config_text)

    def test_never_names_the_daemon_socket_or_api(self) -> None:
        lowered = self.config_text.lower()
        self.assertNotIn("cs71d.sock", lowered)
        self.assertNotIn("/run/cs71", lowered)

    def test_is_one_site_block(self) -> None:
        # A second top-level (unindented) "{" would mean a second site was
        # added without this test being told to expect it.
        opens = [line for line in self.config_lines if not line.startswith(("\t", " ")) and line.endswith("{")]
        self.assertEqual(len(opens), 1)


class InstallScriptIsConsistentWithTheUnitsAndTheCode(unittest.TestCase):
    def setUp(self) -> None:
        self.text = (OPS_DIR / "install.sh").read_text(encoding="utf-8")
        # build_web_workspace/build_daemon_venv live here, not in install.sh
        # itself, so upgrade.sh can call the exact same build without a
        # second copy of the logic to drift out of sync.
        self.common_text = (OPS_DIR / "lib/common.sh").read_text(encoding="utf-8")

    def test_is_valid_posix_shell(self) -> None:
        for script in (
            "install.sh",
            "backup.sh",
            "restore.sh",
            "upgrade.sh",
            "lib/common.sh",
            "tests/smoke-test.sh",
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", "-n", str(OPS_DIR / script)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_writes_the_service_token_to_every_path_the_code_reads(self) -> None:
        common = (OPS_DIR / "lib/common.sh").read_text(encoding="utf-8")
        self.assertIn(DAEMON_SERVICE_TOKEN_PATH, common)
        self.assertIn(WEB_SERVICE_TOKEN_PATH, common)
        self.assertIn(VISION_SERVICE_TOKEN_PATH, common)

    def test_names_every_production_path_it_actually_needs_to_write(self) -> None:
        # The socket path and the device path are never installer-supplied:
        # the daemon binds the one production.example.toml already names, and
        # the udev rule creates the other by its fixed symlink name (checked
        # in UdevRuleMatchesNothingUntilSubstituted). Only the paths this
        # script itself has to write belong in this list.
        for path in (PRODUCTION_WEB_DATABASE_PATH, DAEMON_CONFIG_PATH):
            self.assertIn(path, self.text, f"install.sh never mentions {path}")

    def test_never_hardcodes_a_specific_adapter_identity(self) -> None:
        # The VID/PID/serial are installer arguments, not a value baked in
        # here - no adapter has been approved yet (see the udev rule test).
        self.assertNotIn("idVendor", self.text)
        for flag in ("--vendor-id", "--product-id", "--serial"):
            self.assertIn(flag, self.text)

    def test_prunes_dev_dependencies_but_keeps_tsx_for_the_bootstrap_cli(self) -> None:
        self.assertIn("npm prune --omit=dev", self.common_text)
        self.assertIn("issue-bootstrap-token.ts", self.text)

    def test_every_copied_web_entry_actually_exists(self) -> None:
        # A renamed or removed file in appliance/web must fail here, not as a
        # `cp: cannot stat` in the middle of a real install or upgrade.
        match = re.search(r"for entry in ([^;]+); do", self.common_text)
        self.assertIsNotNone(match, "could not find the web-workspace copy loop")
        entries = match.group(1).split()
        self.assertGreaterEqual(len(entries), 5)
        web_root = REPO_ROOT / "appliance/web"
        for entry in entries:
            self.assertTrue((web_root / entry).exists(), f"appliance/web/{entry} does not exist")

    def test_binds_the_web_service_to_loopback_only(self) -> None:
        self.assertIn("HOST=127.0.0.1", self.text)

    def test_creates_the_token_before_a_service_could_race_it(self) -> None:
        # The comment is the actual, load-bearing design decision, not
        # decoration - assert the code still matches what it says.
        bootstrap_index = self.text.index("issue-bootstrap-token.ts")
        start_index = self.text.index("systemctl restart cs71d.service")
        self.assertLess(
            bootstrap_index,
            start_index,
            "the bootstrap CLI must run before the services start, not after",
        )


class BackupTimerRunsTheInstalledCopy(unittest.TestCase):
    def setUp(self) -> None:
        self.service = parse_unit(OPS_DIR / "systemd/cs71-backup.service")
        self.timer_text = (OPS_DIR / "systemd/cs71-backup.timer").read_text(encoding="utf-8")

    def test_runs_the_copy_install_sh_places_at_a_fixed_path(self) -> None:
        # Not the checkout's own appliance/ops/backup.sh: that path moves or
        # disappears when the checkout is updated or deleted, but the timer
        # still needs to fire.
        self.assertEqual(directive(self.service, "Service", "ExecStart"), "/opt/cs71/ops/backup.sh")

    def test_is_a_oneshot_that_can_write_only_where_backup_sh_needs_to(self) -> None:
        self.assertEqual(directive(self.service, "Service", "Type"), "oneshot")
        paths = directive(self.service, "Service", "ReadWritePaths").split()
        for required in ("/var/lib/cs71d", "/var/lib/cs71-web", "/var/lib/cs71-backups"):
            self.assertIn(required, paths)

    def test_survives_a_missed_run_while_the_appliance_was_off(self) -> None:
        self.assertIn("Persistent=true", self.timer_text)

    def test_install_sh_enables_the_timer_and_installs_its_pinned_copy(self) -> None:
        install_text = (OPS_DIR / "install.sh").read_text(encoding="utf-8")
        self.assertIn("cs71-backup.timer", install_text)
        self.assertIn("/opt/cs71/ops/backup.sh", install_text)


class BackupRestoreUpgradeScriptsAgreeWithTheDaemonsDurabilityMonitor(unittest.TestCase):
    """cs71d's storage_health.py reads what these scripts write; a path or
    shape drifting apart here would silently break that monitor in production
    without any test on either side catching it alone."""

    def setUp(self) -> None:
        self.backup_text = (OPS_DIR / "backup.sh").read_text(encoding="utf-8")
        self.restore_text = (OPS_DIR / "restore.sh").read_text(encoding="utf-8")
        self.upgrade_text = (OPS_DIR / "upgrade.sh").read_text(encoding="utf-8")
        self.storage_health_text = (
            REPO_ROOT / "appliance/daemon/src/cs71d/storage_health.py"
        ).read_text(encoding="utf-8")

    def test_backup_sh_writes_the_marker_path_the_daemon_reads(self) -> None:
        self.assertIn(BACKUP_MARKER_PATH, self.backup_text)
        self.assertIn(f'"{BACKUP_MARKER_PATH}"', self.storage_health_text)

    def test_backup_sh_marker_always_carries_ok_and_completed_at(self) -> None:
        # BackupFreshnessMonitor.check() requires exactly these two fields.
        self.assertIn('"ok"', self.backup_text)
        self.assertIn('"completed_at"', self.backup_text)

    def test_backup_sh_writes_the_marker_on_the_failure_path_too(self) -> None:
        # A backup that silently never updates the marker on failure would
        # leave the monitor reading a stale success forever.
        self.assertIn("trap on_failure ERR", self.backup_text)

    def test_backup_sh_never_includes_a_service_token_in_the_archive(self) -> None:
        # The comment above the config copy step is allowed to say why the
        # credential files are excluded; the script must never actually name
        # either token's path, which is the only way it could touch one.
        self.assertNotIn(DAEMON_SERVICE_TOKEN_PATH, self.backup_text)
        self.assertNotIn(WEB_SERVICE_TOKEN_PATH, self.backup_text)

    def test_restore_sh_stops_web_before_daemon_and_starts_daemon_before_web(self) -> None:
        stop_index = self.restore_text.index("systemctl stop cs71-web.service")
        start_daemon_index = self.restore_text.index("systemctl start cs71d.service")
        start_web_index = self.restore_text.index("systemctl start cs71-web.service")
        self.assertLess(stop_index, start_daemon_index)
        self.assertLess(start_daemon_index, start_web_index)

    def test_restore_sh_verifies_the_manifest_and_integrity_before_installing_anything(self) -> None:
        verify_index = self.restore_text.index("manifest.py\" verify")
        integrity_index = self.restore_text.index("verify_sqlite_integrity")
        install_index = self.restore_text.index("install -o cs71d")
        self.assertLess(verify_index, install_index)
        self.assertLess(integrity_index, install_index)

    def test_upgrade_sh_backs_up_before_touching_the_current_release(self) -> None:
        backup_index = self.upgrade_text.index('"$OPS_DIR/backup.sh"')
        save_index = self.upgrade_text.index("cp -a /opt/cs71/web /opt/cs71/web.previous")
        self.assertLess(backup_index, save_index)

    def test_upgrade_sh_rolls_back_through_restore_sh_on_every_failure_after_the_backup(
        self,
    ) -> None:
        # Every failure once artifacts/data are actually being touched calls
        # rollback() immediately before exiting; the two guard-clause exits
        # before any backup is taken (missing install, failed backup) must
        # not, since there is nothing yet to roll back.
        paired = len(re.findall(r"\n\trollback\n\texit 1\n", self.upgrade_text))
        self.assertEqual(paired, 5)
        self.assertIn('"$OPS_DIR/restore.sh" --from', self.upgrade_text)

    def test_upgrade_sh_starts_daemon_before_web_on_the_new_release(self) -> None:
        daemon_index = self.upgrade_text.index("systemctl start cs71d.service")
        web_index = self.upgrade_text.index("systemctl start cs71-web.service")
        self.assertLess(daemon_index, web_index)

    def test_upgrade_sh_also_rebuilds_cs71_vision(self) -> None:
        self.assertIn("build_vision_venv", self.upgrade_text)

    def test_backup_sh_includes_vision_db_only_when_it_exists(self) -> None:
        self.assertIn("VISION_DB", self.backup_text)
        self.assertIn('[ -f "$VISION_DB" ]', self.backup_text)

    def test_restore_sh_restores_vision_db_only_when_present_in_the_backup(self) -> None:
        self.assertIn('[ -f "$FROM/vision.db" ]', self.restore_text)

    def test_restore_sh_stops_and_starts_cs71_vision_alongside_the_other_services(self) -> None:
        self.assertIn("cs71-vision.service", self.restore_text)

    def test_manifest_py_treats_vision_db_as_optional(self) -> None:
        manifest_text = (OPS_DIR / "lib/manifest.py").read_text(encoding="utf-8")
        self.assertIn('"vision.db"', manifest_text)
        # REQUIRED_FILES itself must not name vision.db - only machine.db and
        # web.db are mandatory for a backup to verify.
        required_line = next(
            line for line in manifest_text.splitlines() if line.startswith("REQUIRED_FILES")
        )
        self.assertNotIn("vision.db", required_line)


class DaemonAndWebConfigAgreeOnTheProductionPaths(unittest.TestCase):
    """A change to either workspace's fixed production paths must not go
    unnoticed by the artifacts that assume today's values."""

    def test_daemon_config_module_still_uses_the_paths_this_suite_assumes(self) -> None:
        text = (
            REPO_ROOT / "appliance/daemon/src/cs71d/config.py"
        ).read_text(encoding="utf-8")
        self.assertIn(f'"{PRODUCTION_DEVICE_PATH}"', text)
        self.assertIn(f'"{PRODUCTION_SOCKET_PATH}"', text)
        self.assertIn(f'"{PRODUCTION_DAEMON_DATABASE_PATH}"', text)
        self.assertIn(f'"{DAEMON_SERVICE_TOKEN_PATH}"', text)

    def test_web_config_module_still_uses_the_paths_this_suite_assumes(self) -> None:
        text = (
            REPO_ROOT / "appliance/web/src/lib/server/config.ts"
        ).read_text(encoding="utf-8")
        self.assertIn(f"'{PRODUCTION_SOCKET_PATH}'", text)
        self.assertIn(f"'{PRODUCTION_WEB_DATABASE_PATH}'", text)
        self.assertIn(f"'{WEB_SERVICE_TOKEN_PATH}'", text)

    def test_production_example_toml_matches_the_fixed_production_paths(self) -> None:
        text = (
            REPO_ROOT / "appliance/daemon/config/production.example.toml"
        ).read_text(encoding="utf-8")
        for path in (
            PRODUCTION_DEVICE_PATH,
            PRODUCTION_SOCKET_PATH,
            PRODUCTION_DAEMON_DATABASE_PATH,
            DAEMON_SERVICE_TOKEN_PATH,
        ):
            self.assertIn(path, text)


class BootstrapCliDependencyIsProductionOnly(unittest.TestCase):
    """tsx has to survive `npm prune --omit=dev`, or install.sh's own bootstrap
    step - which runs after that prune - would fail to find it."""

    def test_tsx_is_a_dependency_not_a_dev_dependency(self) -> None:
        import json

        manifest = json.loads(
            (REPO_ROOT / "appliance/web/package.json").read_text(encoding="utf-8")
        )
        self.assertIn("tsx", manifest.get("dependencies", {}))
        self.assertNotIn("tsx", manifest.get("devDependencies", {}))


if __name__ == "__main__":
    unittest.main()

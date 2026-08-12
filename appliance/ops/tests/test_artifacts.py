"""Structural checks on the installer's own artifacts.

These do not need a Pi, root, or even Linux - they check that the systemd
units, udev rule, Caddyfile and install.sh agree with each other and with the
paths the daemon and web workspaces actually read (appliance/daemon/src/cs71d/config.py,
appliance/web/src/lib/server/config.ts). What they cannot prove is that a real
systemd actually accepts these directives or that the sandboxed processes
still function under them - that is smoke-test.sh's job, and it needs a real
Linux host to run.
"""

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

    def test_the_web_service_starts_after_the_daemon_but_only_wants_it(self) -> None:
        self.assertIn("cs71d.service", directive_words(self.web, "Unit", "After"))
        self.assertIn("cs71d.service", directive_words(self.web, "Unit", "Wants"))
        # Wants, not Requires: a daemon that is not up must not stop the web
        # service from coming up and saying so (SAF-06).
        self.assertNotIn("Unit/Requires", self.web)

    def test_the_web_service_declares_the_fixed_production_origin_env_file(self) -> None:
        self.assertEqual(directive(self.web, "Service", "EnvironmentFile"), "/etc/cs71/web.env")
        self.assertIn("CS71_WEB_PROFILE=production", self.web["Service/Environment"])


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

    def test_is_valid_posix_shell(self) -> None:
        for script in ("install.sh", "lib/common.sh", "tests/smoke-test.sh"):
            with self.subTest(script=script):
                result = subprocess.run(
                    ["bash", "-n", str(OPS_DIR / script)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_writes_the_service_token_to_both_of_the_paths_the_code_reads(self) -> None:
        common = (OPS_DIR / "lib/common.sh").read_text(encoding="utf-8")
        self.assertIn(DAEMON_SERVICE_TOKEN_PATH, common)
        self.assertIn(WEB_SERVICE_TOKEN_PATH, common)

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
        self.assertIn("npm prune --omit=dev", self.text)
        self.assertIn("issue-bootstrap-token.ts", self.text)

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

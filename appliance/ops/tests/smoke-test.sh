#!/usr/bin/env bash
# Functional simulated smoke test for the systemd/user/socket layout PI-OPS-001
# installs.
#
# This runs for real: real system users, the real install.sh, the real
# systemd sandbox directives, a real Unix socket with real kernel-enforced
# permissions. It is meant to run on a real Linux host (the ubuntu-latest CI
# runner this repository's CI job targets), not a container without systemd.
#
# What it deliberately does not do: start cs71d against a real controller.
# Linux DTR is NOT_EXECUTED, so a production-profile config (backend=serial)
# refuses to start on any Linux box - a real Pi included - by design; that is
# not something this script works around. It runs the exact same sandbox
# directives against the simulator backend instead, which is what "simulated"
# in this criterion's own name means. Closing the DTR gate is PI-HIL-001, and
# needs real hardware no CI runner has.

set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPS_DIR="$(cd "$TESTS_DIR/.." && pwd)"
# shellcheck source=../lib/common.sh
source "$OPS_DIR/lib/common.sh"

require_root

cleanup() {
	systemctl stop cs71-web.service cs71d.service cs71-web-smoke.service cs71d-smoke.service \
		>/dev/null 2>&1 || true
	rm -f /etc/systemd/system/cs71d-smoke.service /etc/systemd/system/cs71-web-smoke.service
	rm -f /etc/cs71/cs71d-smoke.toml /dev/cs71 /dev/cs71-smoke-stub
	rm -rf /var/lib/cs71-backups
	# The restore drill below points the real unit names at the smoke config;
	# put them back to what install.sh actually shipped.
	install -m 0644 "$OPS_DIR/systemd/cs71d.service" /etc/systemd/system/cs71d.service
	install -m 0644 "$OPS_DIR/systemd/cs71-web.service" /etc/systemd/system/cs71-web.service
	systemctl daemon-reload >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "== running the real installer =="
"$OPS_DIR/install.sh" --hostname cs71-smoke.invalid --vendor-id DEAD --product-id BEEF \
	--serial SMOKE-0001 --web-port 3000

log "== a stub device node, so file-permission isolation is testable without hardware =="
install -o cs71d -g cs71d -m 0660 /dev/null /dev/cs71-smoke-stub
ln -sf /dev/cs71-smoke-stub /dev/cs71

log "== a test-only daemon config: simulator backend, production-shaped paths =="
# profile=production forces backend=serial (device_path=/dev/cs71), which the
# unqualified Linux DTR gate refuses to open on any Linux host. profile=test
# carries no such requirement while still validating the shape of every path.
cat >/etc/cs71/cs71d-smoke.toml <<'EOF'
[daemon]
profile = "test"
backend = "simulator"
socket_path = "/run/cs71/cs71d.sock"
database_path = "/var/lib/cs71d/machine.db"
service_token_path = "/etc/cs71d/service-token"
EOF
chmod 0644 /etc/cs71/cs71d-smoke.toml

log "== deriving test units: identical sandbox, no device-arrival gate, simulator config =="
sed -e 's#/etc/cs71/cs71d\.toml#/etc/cs71/cs71d-smoke.toml#' \
	-e '/^ConditionPathExists=/d' \
	-e '/^BindsTo=/d' \
	-e 's#^After=local-fs\.target dev-cs71\.device#After=local-fs.target#' \
	"$OPS_DIR/systemd/cs71d.service" >/etc/systemd/system/cs71d-smoke.service
sed -e 's#cs71d\.service#cs71d-smoke.service#g' \
	"$OPS_DIR/systemd/cs71-web.service" >/etc/systemd/system/cs71-web-smoke.service
systemctl daemon-reload

log "== starting the real sandboxed processes =="
systemctl start cs71d-smoke.service
systemctl start cs71-web-smoke.service

for _ in $(seq 1 20); do
	systemctl is-active --quiet cs71d-smoke.service && systemctl is-active --quiet cs71-web-smoke.service && break
	sleep 0.5
done
if ! systemctl is-active --quiet cs71d-smoke.service; then
	journalctl -u cs71d-smoke.service --no-pager
	log "FAIL: cs71d did not stay active under its sandbox"
	exit 1
fi
if ! systemctl is-active --quiet cs71-web-smoke.service; then
	journalctl -u cs71-web-smoke.service --no-pager
	log "FAIL: cs71-web (Node) did not stay active under its sandbox"
	exit 1
fi

log "== the socket has the owner and mode the daemon sets, not a systemd default =="
# systemd's "active (running)" fires the moment the process forks, not once
# it has opened its journal and bound its socket - wait for the socket
# itself rather than assuming service-active already means socket-ready.
for _ in $(seq 1 20); do
	[ -S /run/cs71/cs71d.sock ] && break
	sleep 0.5
done
[ -S /run/cs71/cs71d.sock ] || {
	journalctl -u cs71d-smoke.service --no-pager
	log "FAIL: no socket at /run/cs71/cs71d.sock"
	exit 1
}
mode="$(stat -c '%a' /run/cs71/cs71d.sock)"
[ "$mode" = "660" ] || {
	log "FAIL: socket mode is $mode, expected 660"
	exit 1
}
owner="$(stat -c '%U:%G' /run/cs71/cs71d.sock)"
[ "$owner" = "cs71d:cs71-api" ] || {
	log "FAIL: socket owner is $owner, expected cs71d:cs71-api"
	exit 1
}

log "== the web identity can reach the daemon through the socket with its own credential =="
token="$(cat /etc/cs71-web/service-token)"
status="$(runuser -u cs71-web -- curl -s -o /dev/null -w '%{http_code}' \
	--unix-socket /run/cs71/cs71d.sock -H "Authorization: Bearer $token" \
	http://localhost/v1/health/live)"
[ "$status" = "200" ] || {
	log "FAIL: GET /v1/health/live through the socket returned $status, expected 200"
	exit 1
}

log "== the web identity cannot open the serial-device stub =="
if runuser -u cs71-web -- test -r /dev/cs71 2>/dev/null; then
	log "FAIL: cs71-web can read /dev/cs71; only cs71d may"
	exit 1
fi

log "== the web service itself answers on its loopback port =="
web_status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/login)"
[ "$web_status" = "200" ] || {
	log "FAIL: GET /login on the loopback port returned $web_status, expected 200"
	exit 1
}

log "== the daemon's own sandbox actually refuses to open a TCP socket =="
if systemd-run --pipe --wait --collect \
	--property=User=cs71d --property=Group=cs71-api \
	--property=NoNewPrivileges=yes --property=RestrictAddressFamilies=AF_UNIX \
	python3 -c 'import socket; socket.socket(socket.AF_INET, socket.SOCK_STREAM)' \
	>/tmp/cs71-smoke-af-inet.log 2>&1; then
	cat /tmp/cs71-smoke-af-inet.log
	log "FAIL: a process under the daemon's own sandbox settings opened an AF_INET socket"
	exit 1
fi
rm -f /tmp/cs71-smoke-af-inet.log

log "== a real backup.sh run, against the real databases the smoke units wrote =="
"$OPS_DIR/backup.sh"
BACKUP_DIR="$(find /var/lib/cs71-backups -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"
[ -n "$BACKUP_DIR" ] || {
	log "FAIL: backup.sh reported success but left no backup directory behind"
	exit 1
}
python3 "$OPS_DIR/lib/manifest.py" verify "$BACKUP_DIR"
marker_ok="$(python3 -c 'import json; print(json.load(open("/var/lib/cs71d/backup-status.json"))["ok"])')"
[ "$marker_ok" = "True" ] || {
	log "FAIL: backup-status.json does not record ok=true after a successful backup"
	exit 1
}

log "== a real restore.sh run, through the daemon's own systemd unit names =="
# restore.sh drives cs71d.service/cs71-web.service by their real, fixed
# names - the same ones the daemon's durability monitor and every runbook
# assume. Those names normally boot the production profile, which the
# unqualified Linux DTR gate always refuses to open; pointing them at the
# same simulator-backed content the -smoke units already proved healthy
# lets restore.sh run completely unmodified while still being exercised for
# real, not skipped. cleanup() puts the original units back.
systemctl stop cs71-web-smoke.service cs71d-smoke.service
install -m 0644 /etc/systemd/system/cs71d-smoke.service /etc/systemd/system/cs71d.service
# cs71-web.service's own content never named cs71d-smoke.service in the first
# place (only cs71-web-smoke.service, derived from it, does) - the checked-in
# unit already points at the name just installed above.
install -m 0644 "$OPS_DIR/systemd/cs71-web.service" /etc/systemd/system/cs71-web.service
systemctl daemon-reload

if ! "$OPS_DIR/restore.sh" --from "$BACKUP_DIR" --web-port 3000; then
	journalctl -u cs71d.service -u cs71-web.service --no-pager -n 80
	log "FAIL: restore.sh did not complete against the real unit names"
	exit 1
fi

restored_status="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000/login)"
[ "$restored_status" = "200" ] || {
	log "FAIL: GET /login after restore returned $restored_status, expected 200"
	exit 1
}

log "== all checks passed =="

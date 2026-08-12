#!/usr/bin/env bash
# Install cs71d and cs71-web natively, per docs/architecture/deployment-and-operations.md.
#
# This installs from a source checkout, not a separate release-artifact
# registry: MVP has no such registry yet (docs/architecture/security-and-safety.md
# names "release artifacts" as an asset for when one exists). Run this on the
# target Raspberry Pi itself, as root, from a checkout of this repository -
# building on the target is what guarantees the web workspace's native
# modules (better-sqlite3, @node-rs/argon2) match the Pi's own architecture.
#
# What this script does NOT do: start cs71d against a real controller (the
# Linux DTR gate stays NOT_EXECUTED regardless of this script), acquire a LAN
# TLS certificate for you, or generate the udev VID/PID/serial values - those
# are hardware evidence from PI-HIL-001, supplied as arguments.

set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$OPS_DIR/../.." && pwd)"
# shellcheck source=lib/common.sh
source "$OPS_DIR/lib/common.sh"

WEB_PORT=3000
START=false
HOSTNAME_ARG=""
VENDOR_ID=""
PRODUCT_ID=""
SERIAL=""

usage() {
	cat <<'EOF' >&2
Usage: install.sh --hostname HOST --vendor-id XXXX --product-id XXXX --serial STRING [--web-port PORT] [--start]

  --hostname HOST     LAN hostname Caddy serves (e.g. cs71.local)
  --vendor-id XXXX    approved USB adapter vendor ID, 4 hex digits
  --product-id XXXX   approved USB adapter product ID, 4 hex digits
  --serial STRING     approved adapter's own serial number
  --web-port PORT     loopback port cs71-web listens on (default 3000)
  --start             enable and start services now (default: install and enable only)
EOF
	exit 2
}

while [ $# -gt 0 ]; do
	case "$1" in
	--hostname)
		HOSTNAME_ARG="$2"
		shift 2
		;;
	--vendor-id)
		VENDOR_ID="$2"
		shift 2
		;;
	--product-id)
		PRODUCT_ID="$2"
		shift 2
		;;
	--serial)
		SERIAL="$2"
		shift 2
		;;
	--web-port)
		WEB_PORT="$2"
		shift 2
		;;
	--start)
		START=true
		shift
		;;
	*)
		usage
		;;
	esac
done

[ -n "$HOSTNAME_ARG" ] && [ -n "$VENDOR_ID" ] && [ -n "$PRODUCT_ID" ] && [ -n "$SERIAL" ] || usage

require_root

log "== users and groups =="
ensure_group cs71d
ensure_group cs71-web
ensure_group cs71-api
ensure_system_user cs71d cs71d /var/lib/cs71d
ensure_system_user cs71-web cs71-web /var/lib/cs71-web

log "== directories =="
ensure_dir /opt/cs71/web root:root 0755
ensure_dir /opt/cs71/daemon root:root 0755
# Owner-only, and owned by the identity that reads the credential inside -
# access is by matching UID, not by group membership, so it does not depend
# on which supplementary groups end up on the process at run time.
ensure_dir /etc/cs71 root:root 0755
ensure_dir /etc/cs71d cs71d:cs71d 0700
ensure_dir /etc/cs71-web cs71-web:cs71-web 0700
ensure_dir /var/lib/cs71d cs71d:cs71d 0700
ensure_dir /var/lib/cs71-web cs71-web:cs71-web 0700
# /run/cs71 is systemd's RuntimeDirectory=; it is recreated on every start of
# cs71d.service and is not persisted here.

log "== daemon config =="
# World-readable: every path in this file is already fixed by the production
# profile, so there is nothing here a local account could learn that it could
# not already read from this repository. The credential is a separate file.
install -o root -g root -m 0644 "$SOURCE_ROOT/appliance/daemon/config/production.example.toml" \
	/etc/cs71/cs71d.toml

log "== web config =="
install -o root -g root -m 0644 /dev/null /etc/cs71/web.env
{
	printf 'PORT=%s\n' "$WEB_PORT"
	printf 'HOST=127.0.0.1\n'
	printf 'ORIGIN=https://%s\n' "$HOSTNAME_ARG"
} >/etc/cs71/web.env

log "== service credential =="
if [ ! -s /etc/cs71d/service-token ]; then
	write_service_token_pair "$(generate_token)"
else
	log "a service credential already exists; leaving it in place"
fi

log "== building the web workspace on this host =="
for entry in src scripts static package.json package-lock.json vite.config.ts tsconfig.json; do
	rm -rf "/opt/cs71/web/${entry:?}"
	cp -a "$SOURCE_ROOT/appliance/web/$entry" "/opt/cs71/web/$entry"
done
(
	cd /opt/cs71/web
	npm ci
	npm run build
	# tsx is a runtime dependency (the bootstrap-token CLI needs it after this
	# prune); the rest of devDependencies is not needed once build/ exists.
	npm prune --omit=dev
)
chown -R root:root /opt/cs71/web

log "== installing the daemon into a dedicated venv =="
python3 -m venv /opt/cs71/daemon/venv
/opt/cs71/daemon/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
	"$SOURCE_ROOT/host" "$SOURCE_ROOT/appliance/daemon"
chown -R root:root /opt/cs71/daemon

log "== systemd units =="
install -m 0644 "$OPS_DIR/systemd/cs71d.service" /etc/systemd/system/cs71d.service
install -m 0644 "$OPS_DIR/systemd/cs71-web.service" /etc/systemd/system/cs71-web.service
mkdir -p /etc/systemd/system/caddy.service.d
install -m 0644 "$OPS_DIR/systemd/caddy-cs71.conf" /etc/systemd/system/caddy.service.d/cs71.conf

log "== udev rule =="
substitute_template "$OPS_DIR/udev/99-cs71.rules" /etc/udev/rules.d/99-cs71.rules \
	"VENDOR_ID=$VENDOR_ID" "PRODUCT_ID=$PRODUCT_ID" "SERIAL=$SERIAL"

log "== Caddy site =="
# /etc/caddy may not exist yet if the Caddy package has not been installed
# on this host before this script runs; the site config does not need the
# package present to be written.
mkdir -p /etc/caddy
substitute_template "$OPS_DIR/caddy/Caddyfile.cs71" /etc/caddy/Caddyfile \
	"HOSTNAME=$HOSTNAME_ARG" "WEB_PORT=$WEB_PORT"

log "== reloading systemd and udev =="
systemctl daemon-reload
if command -v udevadm >/dev/null 2>&1; then
	udevadm control --reload-rules
	udevadm trigger --subsystem-match=tty
fi

log "== enabling services =="
systemctl enable cs71d.service cs71-web.service

if [ "$START" = true ]; then
	# The bootstrap CLI is deliberately the first thing to open web.db, as the
	# cs71-web identity: it creates and migrates the database, and the service
	# started next just opens what is already there. Starting the service
	# first would make two processes race to migrate the same empty file.
	log "== bootstrap token =="
	runuser -u cs71-web -- env CS71_WEB_DATABASE_PATH=/var/lib/cs71-web/web.db \
		/opt/cs71/web/node_modules/.bin/tsx /opt/cs71/web/scripts/issue-bootstrap-token.ts

	log "== starting services =="
	systemctl restart cs71d.service
	systemctl restart cs71-web.service
	systemctl try-restart caddy.service || log "caddy.service is not active; start it once its TLS/DNS prerequisites are ready"
else
	log "services are installed and enabled but not started (pass --start to start them)"
fi

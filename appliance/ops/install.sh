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
CAMERA_VENDOR_ID=""
CAMERA_PRODUCT_ID=""

usage() {
	cat <<'EOF' >&2
Usage: install.sh --hostname HOST --vendor-id XXXX --product-id XXXX --serial STRING [--web-port PORT] [--camera-vendor-id XXXX --camera-product-id XXXX] [--start]

  --hostname HOST          LAN hostname Caddy serves (e.g. cs71.local)
  --vendor-id XXXX         approved USB adapter vendor ID, 4 hex digits
  --product-id XXXX        approved USB adapter product ID, 4 hex digits
  --serial STRING          approved adapter's own serial number
  --web-port PORT          loopback port cs71-web listens on (default 3000)
  --camera-vendor-id XXXX  classifier camera vendor ID, 4 hex digits (optional)
  --camera-product-id XXXX classifier camera product ID, 4 hex digits (optional)
  --start                  enable and start services now (default: install and enable only)

cs71-vision is always installed and enabled. Without the two --camera-*
arguments its udev rule matches no real device, the same way the checked-in
rule does before substitution - install it again with those arguments once
the camera's hardware identity is recorded (PI-HIL-001-class evidence).
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
	--camera-vendor-id)
		CAMERA_VENDOR_ID="$2"
		shift 2
		;;
	--camera-product-id)
		CAMERA_PRODUCT_ID="$2"
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
ensure_group cs71-vision
ensure_system_user cs71d cs71d /var/lib/cs71d
ensure_system_user cs71-web cs71-web /var/lib/cs71-web
ensure_system_user cs71-vision cs71-vision /var/lib/cs71-vision
# Real OS-level membership, in addition to (not instead of) cs71-web.service's
# and cs71-vision.service's own SupplementaryGroups=cs71-api: that directive
# is systemd's own grant and needs no /etc/group entry, but anything reaching
# these identities a different way - su, runuser, a technician's shell - only
# sees real group membership.
ensure_group_member cs71-api cs71-web
ensure_group_member cs71-api cs71-vision

log "== directories =="
ensure_dir /opt/cs71/web root:root 0755
ensure_dir /opt/cs71/daemon root:root 0755
ensure_dir /opt/cs71/vision root:root 0755
# Owner-only, and owned by the identity that reads the credential inside -
# access is by matching UID, not by group membership, so it does not depend
# on which supplementary groups end up on the process at run time.
ensure_dir /etc/cs71 root:root 0755
ensure_dir /etc/cs71d cs71d:cs71d 0700
ensure_dir /etc/cs71-web cs71-web:cs71-web 0700
ensure_dir /etc/cs71-vision cs71-vision:cs71-vision 0700
ensure_dir /var/lib/cs71d cs71d:cs71d 0700
ensure_dir /var/lib/cs71-web cs71-web:cs71-web 0700
# Where PI-VISION-002's self-labeled dataset (vision.db) lives; also created
# by cs71-vision.service's own StateDirectory=, this just makes it exist
# before the service first starts too.
ensure_dir /var/lib/cs71-vision cs71-vision:cs71-vision 0700
# Owned by root, not either service identity: appliance/ops/backup.sh and
# restore.sh run as root and are the only things that ever touch it.
ensure_dir /var/lib/cs71-backups root:root 0700
# /run/cs71 is systemd's RuntimeDirectory=; it is recreated on every start of
# cs71d.service and is not persisted here.

log "== daemon config =="
# World-readable: every path in this file is already fixed by the production
# profile, so there is nothing here a local account could learn that it could
# not already read from this repository. The credential is a separate file.
install -o root -g root -m 0644 "$SOURCE_ROOT/appliance/daemon/config/production.example.toml" \
	/etc/cs71/cs71d.toml

log "== vision config =="
# World-readable for the same reason cs71d.toml is: every value is already
# fixed by the production profile, nothing secret in it.
install -o root -g root -m 0644 "$SOURCE_ROOT/appliance/vision/config/production.example.toml" \
	/etc/cs71/cs71vision.toml

log "== web config =="
install -o root -g root -m 0644 /dev/null /etc/cs71/web.env
{
	printf 'PORT=%s\n' "$WEB_PORT"
	printf 'HOST=127.0.0.1\n'
	printf 'ORIGIN=https://%s\n' "$HOSTNAME_ARG"
} >/etc/cs71/web.env

log "== service credential =="
if [ ! -s /etc/cs71d/service-token ]; then
	write_service_tokens "$(generate_token)"
else
	log "a service credential already exists; leaving it in place"
fi

log "== building the web workspace on this host =="
build_web_workspace "$SOURCE_ROOT"

log "== installing the daemon into a dedicated venv =="
build_daemon_venv "$SOURCE_ROOT"

log "== installing cs71-vision into a dedicated venv =="
build_vision_venv "$SOURCE_ROOT"

log "== recording what was actually installed, for backup.sh's manifest =="
write_release_info "$SOURCE_ROOT"

log "== installing the backup script the timer runs =="
# backup.sh has to keep working after this checkout moves or is updated, so
# it - and the manifest helper it calls - are copied here rather than
# referenced from the checkout. restore.sh and upgrade.sh are not: they
# rebuild from source and are meant to be run from an up-to-date checkout,
# the same way install.sh itself is.
mkdir -p /opt/cs71/ops/lib
install -m 0755 "$OPS_DIR/backup.sh" /opt/cs71/ops/backup.sh
install -m 0644 "$OPS_DIR/lib/common.sh" /opt/cs71/ops/lib/common.sh
install -m 0644 "$OPS_DIR/lib/manifest.py" /opt/cs71/ops/lib/manifest.py

log "== systemd units =="
install -m 0644 "$OPS_DIR/systemd/cs71d.service" /etc/systemd/system/cs71d.service
install -m 0644 "$OPS_DIR/systemd/cs71-web.service" /etc/systemd/system/cs71-web.service
install -m 0644 "$OPS_DIR/systemd/cs71-backup.service" /etc/systemd/system/cs71-backup.service
install -m 0644 "$OPS_DIR/systemd/cs71-backup.timer" /etc/systemd/system/cs71-backup.timer
install -m 0644 "$OPS_DIR/systemd/cs71-vision.service" /etc/systemd/system/cs71-vision.service
mkdir -p /etc/systemd/system/caddy.service.d
install -m 0644 "$OPS_DIR/systemd/caddy-cs71.conf" /etc/systemd/system/caddy.service.d/cs71.conf

log "== udev rule (controller) =="
substitute_template "$OPS_DIR/udev/99-cs71.rules" /etc/udev/rules.d/99-cs71.rules \
	"VENDOR_ID=$VENDOR_ID" "PRODUCT_ID=$PRODUCT_ID" "SERIAL=$SERIAL"

log "== udev rule (classifier camera) =="
if [ -n "$CAMERA_VENDOR_ID" ] && [ -n "$CAMERA_PRODUCT_ID" ]; then
	substitute_template "$OPS_DIR/udev/98-cs71-vision.rules" /etc/udev/rules.d/98-cs71-vision.rules \
		"CAMERA_VENDOR_ID=$CAMERA_VENDOR_ID" "CAMERA_PRODUCT_ID=$CAMERA_PRODUCT_ID"
else
	install -m 0644 "$OPS_DIR/udev/98-cs71-vision.rules" /etc/udev/rules.d/98-cs71-vision.rules
	log "no --camera-vendor-id/--camera-product-id given; cs71-vision.service is installed" \
		"and enabled but matches no real device yet"
fi

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
	udevadm trigger --subsystem-match=video4linux
fi

log "== enabling services =="
systemctl enable cs71d.service cs71-web.service cs71-backup.timer cs71-vision.service

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
	systemctl restart cs71-backup.timer
	# Harmless when no camera is matched yet: BindsTo=/ConditionPathExists=
	# on cs71-vision.service make this a no-op start rather than a failure,
	# the same way cs71d.service behaves before its own adapter is present.
	systemctl restart cs71-vision.service
	systemctl try-restart caddy.service || log "caddy.service is not active; start it once its TLS/DNS prerequisites are ready"
else
	log "services are installed and enabled but not started (pass --start to start them)"
fi

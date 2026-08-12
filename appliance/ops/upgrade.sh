#!/usr/bin/env bash
# Upgrade cs71d/cs71-web in place from this checkout: back up, stop web then
# daemon, rebuild, start daemon then web, smoke test. Anything that fails
# before cs71-web is confirmed healthy on the new build rolls back the
# release artifacts and the pre-upgrade database/config snapshot, and starts
# the previous release the same way restore.sh does.
#
# Run this the same way install.sh is run: as root, from an up-to-date
# checkout of this repository, on the target machine itself - so the web
# workspace's native modules are built for the machine they run on. There is
# no separate release-artifact registry in MVP (see install.sh's own header),
# so "the new release" here means whatever this checkout currently contains.
#
# There is no standalone migration-runner command: opening machine.db and
# web.db is itself the forward-only, checksummed migration
# (appliance/daemon/src/cs71d/journal.py, appliance/web/src/lib/server/auth/database.ts),
# so "apply migrations" and "start the daemon"/"start the web service" below
# are the same step, not two.

set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_ROOT="$(cd "$OPS_DIR/../.." && pwd)"
# shellcheck source=lib/common.sh
source "$OPS_DIR/lib/common.sh"

WEB_PORT=3000

usage() {
	cat <<'EOF' >&2
Usage: upgrade.sh [--web-port PORT]

  --web-port PORT   loopback port cs71-web listens on (default 3000)
EOF
	exit 2
}

while [ $# -gt 0 ]; do
	case "$1" in
	--web-port)
		WEB_PORT="$2"
		shift 2
		;;
	*)
		usage
		;;
	esac
done

require_root
[ -d /opt/cs71/web ] && [ -d /opt/cs71/daemon ] || {
	log "no existing installation at /opt/cs71; run install.sh first"
	exit 1
}

log "== checking free disk space =="
require_free_disk /var 500
require_free_disk /opt 500

log "== pre-upgrade backup =="
"$OPS_DIR/backup.sh"
LATEST_BACKUP="$(find /var/lib/cs71-backups -mindepth 1 -maxdepth 1 -type d | sort | tail -n1)"
[ -n "$LATEST_BACKUP" ] || {
	log "backup.sh reported success but left no backup directory behind"
	exit 1
}
log "pre-upgrade backup: $LATEST_BACKUP"

log "== saving the current release, in case this upgrade must roll back =="
rm -rf /opt/cs71/web.previous /opt/cs71/daemon.previous
cp -a /opt/cs71/web /opt/cs71/web.previous
cp -a /opt/cs71/daemon /opt/cs71/daemon.previous

ROLLED_BACK=false
rollback() {
	if [ "$ROLLED_BACK" = true ]; then
		return
	fi
	ROLLED_BACK=true
	log "== rolling back to the pre-upgrade release and data =="
	systemctl stop cs71-web.service cs71d.service || true
	rm -rf /opt/cs71/web /opt/cs71/daemon
	mv /opt/cs71/web.previous /opt/cs71/web
	mv /opt/cs71/daemon.previous /opt/cs71/daemon
	# restore.sh stops services (already stopped, harmless), restores the
	# pre-upgrade database/config snapshot, verifies integrity, then starts
	# daemon then web with its own smoke test - exactly what a rollback needs.
	if "$OPS_DIR/restore.sh" --from "$LATEST_BACKUP" --web-port "$WEB_PORT"; then
		log "UPGRADE ROLLED BACK: restored the previous release and $LATEST_BACKUP"
	else
		log "UPGRADE ROLLBACK FAILED: manual recovery is required;" \
			"see docs/architecture/deployment-and-operations.md's restore runbook"
	fi
}

log "== stopping services (web, then daemon) =="
systemctl stop cs71-web.service cs71d.service || true

log "== building the new release =="
if ! build_web_workspace "$SOURCE_ROOT"; then
	log "web workspace build failed"
	rollback
	exit 1
fi
if ! build_daemon_venv "$SOURCE_ROOT"; then
	log "daemon venv build failed"
	rollback
	exit 1
fi
write_release_info "$SOURCE_ROOT"

log "== starting the daemon on the new release (this is what applies its migration) =="
systemctl start cs71d.service
if ! daemon_smoke_test 30; then
	journalctl -u cs71d.service --no-pager -n 50 || true
	log "FAIL: cs71d did not come up healthy on the new release"
	rollback
	exit 1
fi

log "== starting the web service on the new release (this is what applies its migration) =="
systemctl start cs71-web.service
if ! web_smoke_test 30 "$WEB_PORT"; then
	journalctl -u cs71-web.service --no-pager -n 50 || true
	log "FAIL: cs71-web did not come up healthy on the new release"
	rollback
	exit 1
fi

rm -rf /opt/cs71/web.previous /opt/cs71/daemon.previous
log "UPGRADE COMPLETE"

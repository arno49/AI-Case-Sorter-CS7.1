#!/usr/bin/env bash
# Restore machine.db, web.db and configuration from a backup.sh snapshot.
#
# Runs against stopped services, verifies the backup's manifest checksums and
# SQLite integrity before installing anything, then starts the daemon before
# the web service and proves each one actually answers - the "restore is
# validated by integrity check and application read-only smoke test before
# production use" contract in docs/architecture/data-and-persistence.md.
#
# Never assumes an interrupted write completed: if any step here fails, the
# script stops rather than starting a service against a half-restored store,
# and the runbook (docs/architecture/deployment-and-operations.md) takes over
# from there.

set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$OPS_DIR/lib/common.sh"

WEB_PORT=3000
FROM=""

usage() {
	cat <<'EOF' >&2
Usage: restore.sh --from BACKUP_DIR [--web-port PORT]

  --from BACKUP_DIR   a directory backup.sh produced (contains manifest.json)
  --web-port PORT     loopback port cs71-web listens on (default 3000)
EOF
	exit 2
}

while [ $# -gt 0 ]; do
	case "$1" in
	--from)
		FROM="$2"
		shift 2
		;;
	--web-port)
		WEB_PORT="$2"
		shift 2
		;;
	*)
		usage
		;;
	esac
done

[ -n "$FROM" ] || usage
[ -d "$FROM" ] || {
	log "no such backup directory: $FROM"
	exit 1
}

require_root

log "== verifying the manifest matches its files =="
python3 "$OPS_DIR/lib/manifest.py" verify "$FROM"

log "== verifying SQLite integrity of the backup copies, before touching anything live =="
verify_sqlite_integrity "$FROM/machine.db"
verify_sqlite_integrity "$FROM/web.db"

log "== stopping services (web, then daemon) =="
systemctl stop cs71-web.service cs71d.service || true

log "== restoring the databases =="
install -o cs71d -g cs71d -m 0600 "$FROM/machine.db" /var/lib/cs71d/machine.db
install -o cs71-web -g cs71-web -m 0600 "$FROM/web.db" /var/lib/cs71-web/web.db
# Stale WAL/SHM siblings from the previous database must not shadow the
# restored file's own write-ahead log.
rm -f /var/lib/cs71d/machine.db-wal /var/lib/cs71d/machine.db-shm
rm -f /var/lib/cs71-web/web.db-wal /var/lib/cs71-web/web.db-shm

if [ -f "$FROM/cs71d.toml" ]; then
	log "== restoring the daemon configuration =="
	install -o root -g root -m 0644 "$FROM/cs71d.toml" /etc/cs71/cs71d.toml
fi
if [ -f "$FROM/web.env" ]; then
	log "== restoring the web configuration =="
	install -o root -g root -m 0644 "$FROM/web.env" /etc/cs71/web.env
fi

log "== starting the daemon and verifying it actually answers =="
systemctl start cs71d.service
if ! daemon_smoke_test 30; then
	journalctl -u cs71d.service --no-pager -n 50 || true
	log "FAIL: cs71d did not answer after restore; see the journal above"
	exit 1
fi

log "== starting the web service and verifying it actually answers =="
systemctl start cs71-web.service
if ! web_smoke_test 30 "$WEB_PORT"; then
	journalctl -u cs71-web.service --no-pager -n 50 || true
	log "FAIL: cs71-web did not answer after restore; see the journal above"
	exit 1
fi

log "restore complete from $FROM"

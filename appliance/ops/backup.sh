#!/usr/bin/env bash
# Consistent SQLite backup of machine.db and web.db, plus configuration, into
# a checksummed, versioned manifest under /var/lib/cs71-backups.
#
# Safe to run while cs71d/cs71-web are serving: `sqlite3 ... .backup` is
# SQLite's own online-backup method (docs/architecture/data-and-persistence.md's
# "SQLite-consistent backup") and does not require stopping either writer.
# This is what appliance/ops/systemd/cs71-backup.timer runs daily, and what
# cs71d's own BackupFreshnessMonitor (appliance/daemon/src/cs71d/storage_health.py)
# reads the result of before admitting new work.
#
# Runs from either a checkout (`appliance/ops/backup.sh`, manually) or its
# installed copy (`/opt/cs71/ops/backup.sh`, the timer) - both lay out
# lib/common.sh and lib/manifest.py the same way relative to this script.

set -euo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "$OPS_DIR/lib/common.sh"

BACKUP_ROOT=/var/lib/cs71-backups
MARKER=/var/lib/cs71d/backup-status.json
MACHINE_DB=/var/lib/cs71d/machine.db
WEB_DB=/var/lib/cs71-web/web.db
DAEMON_CONFIG=/etc/cs71/cs71d.toml
WEB_CONFIG=/etc/cs71/web.env
RELEASE_INFO=/opt/cs71/ops/release-info.json

require_root

# The daemon's BackupFreshnessMonitor treats this file's absence, a false
# "ok", or an old "completed_at" identically: new work is blocked until a
# backup can be shown to have actually succeeded. Every exit path below - the
# happy path and every early failure - writes it exactly once.
record_status() {
	local ok="$1" detail="$2"
	mkdir -p "$(dirname "$MARKER")"
	python3 - "$MARKER" "$ok" "$detail" <<'PY'
import datetime
import json
import sys

marker_path, ok, detail = sys.argv[1], sys.argv[2] == "true", sys.argv[3]
document = {
    "ok": ok,
    "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "detail": detail,
}
with open(marker_path, "w", encoding="utf-8") as stream:
    json.dump(document, stream)
PY
	chmod 0600 "$MARKER"
	chown cs71d:cs71d "$MARKER" 2>/dev/null || true
}

on_failure() {
	# No secrets in this message: a path and a fixed reason, nothing read
	# from the databases or configuration themselves.
	record_status false "backup failed; see the daemon and system journals"
}
trap on_failure ERR

[ -f "$MACHINE_DB" ] || {
	log "no journal at $MACHINE_DB; nothing to back up yet"
	exit 1
}
[ -f "$WEB_DB" ] || {
	log "no web database at $WEB_DB; nothing to back up yet"
	exit 1
}

require_free_disk /var 200

NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
DEST="$BACKUP_ROOT/${NOW//[:-]/}"
mkdir -p "$DEST"
chmod 0700 "$DEST"

log "== backing up machine.db =="
sqlite3 "$MACHINE_DB" ".backup '$DEST/machine.db'"
log "== backing up web.db =="
sqlite3 "$WEB_DB" ".backup '$DEST/web.db'"

log "== verifying the backup copies are not corrupt =="
verify_sqlite_integrity "$DEST/machine.db"
verify_sqlite_integrity "$DEST/web.db"

log "== copying configuration (no secrets: service-token files are excluded) =="
install -o root -g root -m 0600 "$DAEMON_CONFIG" "$DEST/cs71d.toml"
install -o root -g root -m 0600 "$WEB_CONFIG" "$DEST/web.env"
chmod 0600 "$DEST/machine.db" "$DEST/web.db"
chown root:root "$DEST"/*.db

log "== writing the manifest =="
python3 "$OPS_DIR/lib/manifest.py" write "$DEST" "$RELEASE_INFO" "$NOW" >"$DEST/manifest.json"
chmod 0600 "$DEST/manifest.json"
chown root:root "$DEST/manifest.json"

trap - ERR
record_status true "$DEST"
log "backup complete: $DEST"

#!/usr/bin/env bash
# Shared functions for install.sh and smoke-test.sh.
#
# Kept in one file on purpose: the smoke test's whole point is to prove the
# production privilege layout actually works, which it cannot do if it builds
# that layout a different way than the installer does.

set -euo pipefail

log() {
	printf '%s\n' "$*" >&2
}

require_root() {
	if [ "$(id -u)" -ne 0 ]; then
		log "this must run as root: it creates system users and writes root-owned files"
		exit 1
	fi
}

ensure_group() {
	local name="$1"
	if ! getent group "$name" >/dev/null; then
		groupadd --system "$name"
		log "created group $name"
	fi
}

# $1 username  $2 primary group (already created)  $3 home/state directory
ensure_system_user() {
	local name="$1" group="$2" home="$3"
	if ! id "$name" >/dev/null 2>&1; then
		useradd --system --gid "$group" --home-dir "$home" --no-create-home \
			--shell /usr/sbin/nologin "$name"
		log "created user $name"
	fi
}

# $1 group  $2 user - idempotent. The systemd units grant supplementary
# groups themselves (SupplementaryGroups=), which needs no OS-level
# membership at all; this is for everything that reaches these identities
# a different way (su, runuser, a technician's own shell) to see the same
# group access systemd would have given them.
ensure_group_member() {
	local group="$1" user="$2"
	usermod -aG "$group" "$user"
}

# $1 path  $2 owner:group  $3 octal mode
ensure_dir() {
	local path="$1" owner="$2" mode="$3"
	mkdir -p "$path"
	chown "$owner" "$path"
	chmod "$mode" "$path"
}

# 256 bits, URL-safe alphabet - the same shape `createToken()` produces on the
# web side, though this and that value are never compared to one another.
generate_token() {
	if command -v openssl >/dev/null 2>&1; then
		openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
	else
		head -c 32 /dev/urandom | base64 | tr '+/' '-_' | tr -d '='
	fi
}

# Write the same credential to every service identity's own copy. Three
# files, not one, because cs71d, cs71-web and cs71-vision are separate
# identities that happen to need to agree on one shared secret - see
# appliance/ops/README.md. Each file is created with its final owner and
# mode *before* any content is written to it, so the secret is never briefly
# sitting under a wrong mode.
write_service_tokens() {
	local token="$1"
	install -o cs71d -g cs71d -m 0600 /dev/null /etc/cs71d/service-token
	printf '%s\n' "$token" >/etc/cs71d/service-token
	install -o cs71-web -g cs71-web -m 0600 /dev/null /etc/cs71-web/service-token
	printf '%s\n' "$token" >/etc/cs71-web/service-token
	install -o cs71-vision -g cs71-vision -m 0600 /dev/null /etc/cs71-vision/service-token
	printf '%s\n' "$token" >/etc/cs71-vision/service-token
}

# The machine actor kind's own credential (PI-VISION-007, ADR-0013) - a
# distinct secret from write_service_tokens' shared one, held by only the two
# identities that ever need it: cs71d (which validates it) and cs71-vision
# (which will present it once PI-VISION-008 wires that up). cs71-web never
# receives this file - it has no legitimate reason to ever act as the
# machine actor, and must not be able to forge that attribution.
write_machine_service_token() {
	local token="$1"
	install -o cs71d -g cs71d -m 0600 /dev/null /etc/cs71d/machine-service-token
	printf '%s\n' "$token" >/etc/cs71d/machine-service-token
	install -o cs71-vision -g cs71-vision -m 0600 /dev/null /etc/cs71-vision/machine-service-token
	printf '%s\n' "$token" >/etc/cs71-vision/machine-service-token
}

# $1 source template  $2 destination  remaining args are NAME=value, replacing
# every @@NAME@@ in the template.
substitute_template() {
	local src="$1" dest="$2"
	shift 2
	local content
	content="$(cat "$src")"
	local pair name value
	for pair in "$@"; do
		name="${pair%%=*}"
		value="${pair#*=}"
		content="${content//@@${name}@@/${value}}"
	done
	printf '%s' "$content" >"$dest"
}

# $1 source checkout root - build the SvelteKit workspace into /opt/cs71/web.
# Shared by install.sh and upgrade.sh so they build the release the same way;
# upgrade.sh keeping its own copy of this would be exactly the kind of drift
# smoke-test.sh already exists to catch for the systemd side.
build_web_workspace() {
	local source_root="$1"
	local entry
	for entry in src scripts static package.json package-lock.json vite.config.ts tsconfig.json; do
		rm -rf "/opt/cs71/web/${entry:?}"
		cp -a "$source_root/appliance/web/$entry" "/opt/cs71/web/$entry"
	done
	(
		cd /opt/cs71/web
		npm ci
		npm run build
		# tsx is a runtime dependency (the bootstrap-token CLI needs it after
		# this prune); the rest of devDependencies is not needed once build/
		# exists.
		npm prune --omit=dev
	)
	chown -R root:root /opt/cs71/web
}

# $1 source checkout root - (re)install cs71d into a dedicated venv.
build_daemon_venv() {
	local source_root="$1"
	rm -rf /opt/cs71/daemon/venv
	python3 -m venv /opt/cs71/daemon/venv
	/opt/cs71/daemon/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
		"$source_root/host" "$source_root/appliance/daemon"
	chown -R root:root /opt/cs71/daemon
}

# $1 source checkout root - (re)install cs71-vision into its own dedicated
# venv, separate from cs71d's: it has its own dependency set
# (opencv-python-headless, numpy) that has no reason to share cs71d's venv.
build_vision_venv() {
	local source_root="$1"
	rm -rf /opt/cs71/vision/venv
	python3 -m venv /opt/cs71/vision/venv
	/opt/cs71/vision/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
		"$source_root/appliance/vision"
	chown -R root:root /opt/cs71/vision
}

# $1 source checkout root - record what was actually built and installed, so
# a later backup (from a periodic timer, with no checkout beside it) can
# still say what version it backed up. install.sh and upgrade.sh both call
# this right after build_web_workspace/build_daemon_venv succeed.
write_release_info() {
	local source_root="$1" lib_dir
	lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
	mkdir -p /opt/cs71/ops
	python3 "$lib_dir/manifest.py" release-info "$source_root" \
		/opt/cs71/ops/release-info.json "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	chmod 0644 /opt/cs71/ops/release-info.json
}

# $1 path  $2 minimum free MiB - refuse to proceed on a nearly-full disk
# rather than leave a backup, upgrade or restore half-written.
require_free_disk() {
	local path="$1" minimum_mib="$2" free_kib
	free_kib="$(df -Pk "$path" | awk 'NR==2 {print $4}')"
	if [ "${free_kib:-0}" -lt "$((minimum_mib * 1024))" ]; then
		log "only ${free_kib:-0}KiB free at $path, below the ${minimum_mib}MiB floor"
		return 1
	fi
}

# $1 sqlite file - refuse to install a database PRAGMA integrity_check does
# not report clean, whether it is a backup copy about to be restored or one
# already sitting in production.
verify_sqlite_integrity() {
	local db="$1" result
	result="$(sqlite3 "$db" "PRAGMA integrity_check;")"
	if [ "$result" != "ok" ]; then
		log "integrity check failed for $db: $result"
		return 1
	fi
}

# $1 timeout seconds - poll for the daemon's runtime socket rather than
# trusting `systemctl is-active`, which reports "active (running)" the
# instant a Type=simple process forks, well before it has bound anything.
wait_for_daemon_socket() {
	local timeout="$1" waited=0
	while [ ! -S /run/cs71/cs71d.sock ]; do
		sleep 1
		waited=$((waited + 1))
		if [ "$waited" -ge "$timeout" ]; then
			log "cs71d never created /run/cs71/cs71d.sock"
			return 1
		fi
	done
}

# $1 timeout seconds - a read-only GET through the daemon's own socket, with
# its own service credential: proof the process actually answers, not just
# that systemd thinks it is running.
daemon_smoke_test() {
	local timeout="$1" token status
	wait_for_daemon_socket "$timeout" || return 1
	token="$(cat /etc/cs71d/service-token)"
	status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
		--unix-socket /run/cs71/cs71d.sock -H "Authorization: Bearer $token" \
		http://localhost/v1/health/live)"
	if [ "$status" != "200" ]; then
		log "GET /v1/health/live through the daemon socket returned $status, expected 200"
		return 1
	fi
}

# $1 timeout seconds - a read-only GET through cs71-vision's own dataset api
# socket (PI-VISION-003), with the shared service credential. Only checked
# when the socket actually appears within the timeout: a fresh install with
# no --camera-vendor-id/--camera-product-id, or one whose camera has not
# arrived yet, never brings cs71-vision.service up at all
# (ConditionPathExists=) and that absence is not a failure here.
vision_smoke_test() {
	local timeout="$1" waited=0 token status
	while [ ! -S /run/cs71-vision/cs71vision.sock ]; do
		sleep 1
		waited=$((waited + 1))
		if [ "$waited" -ge "$timeout" ]; then
			return 1
		fi
	done
	token="$(cat /etc/cs71-web/service-token)"
	status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
		--unix-socket /run/cs71-vision/cs71vision.sock -H "Authorization: Bearer $token" \
		http://localhost/v1/dataset)"
	if [ "$status" != "200" ]; then
		log "GET /v1/dataset through cs71-vision's socket returned $status, expected 200"
		return 1
	fi
}

# $1 timeout seconds  $2 loopback port - a read-only, unauthenticated GET on
# the web service's own port; the login page still answers 200 fresh.
web_smoke_test() {
	local timeout="$1" port="$2" waited=0 status
	while :; do
		status="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 \
			"http://127.0.0.1:$port/login" || true)"
		[ "$status" = "200" ] && return 0
		waited=$((waited + 1))
		if [ "$waited" -ge "$timeout" ]; then
			log "GET /login on 127.0.0.1:$port returned '$status', expected 200"
			return 1
		fi
		sleep 1
	done
}

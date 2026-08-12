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

# Write the same credential to both service identities' own copies. Two
# files, not one, because cs71d and cs71-web are separate identities that
# happen to need to agree on one shared secret - see appliance/ops/README.md.
# The file is created with its final owner and mode *before* any content is
# written to it, so the secret is never briefly sitting under a wrong mode.
write_service_token_pair() {
	local token="$1"
	install -o cs71d -g cs71d -m 0600 /dev/null /etc/cs71d/service-token
	printf '%s\n' "$token" >/etc/cs71d/service-token
	install -o cs71-web -g cs71-web -m 0600 /dev/null /etc/cs71-web/service-token
	printf '%s\n' "$token" >/etc/cs71-web/service-token
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

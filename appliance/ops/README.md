# Appliance packaging and lifecycle: systemd, udev, Caddy, backup/upgrade/restore

What `install.sh` builds on a Raspberry Pi (or any Debian-family Linux host,
for the smoke test): two least-privilege systemd services, a udev rule that
gives one of them exclusive access to one specific USB adapter, and a Caddy
site that fronts the other on the LAN. This mirrors
[deployment-and-operations.md](../../docs/architecture/deployment-and-operations.md)
and [ADR-0009](../../docs/architecture/adr/0009-native-systemd-udev-caddy.md);
where this document and that one differ on a detail, this one is the one the
installer actually does. `backup.sh`, `restore.sh` and `upgrade.sh` (PI-OPS-002)
are the maintenance half of the same document's "Install, upgrade and rollback"
and "Backups, observability and runbooks" sections.

## Layout

```text
/opt/cs71/web/                 SvelteKit source + a fresh npm ci/build, pruned to dependencies
/opt/cs71/daemon/venv/         a dedicated Python venv with cs71_protocol + cs71d installed into it
/opt/cs71/vision/venv/         a dedicated Python venv with cs71vision installed into it
/opt/cs71/ops/                 backup.sh's own installed copy, plus release-info.json (see below)
/etc/cs71/cs71d.toml           daemon config — world-readable, nothing secret in it
/etc/cs71/cs71vision.toml      vision config — world-readable, nothing secret in it
/etc/cs71/web.env              web PORT/HOST/ORIGIN — world-readable, nothing secret in it
/etc/cs71d/service-token       the daemon's own copy of the shared credential, cs71d:cs71d 0600
/etc/cs71-web/service-token    the web's own copy of the same credential, cs71-web:cs71-web 0600
/etc/cs71-vision/service-token the vision service's own copy of the same credential, cs71-vision:cs71-vision 0600
/var/lib/cs71d/machine.db      daemon-owned SQLite, cs71d:cs71d 0700 directory, 0600 file
/var/lib/cs71d/backup-status.json  the daemon's own BackupFreshnessMonitor reads this, cs71d:cs71d 0600
/var/lib/cs71-web/web.db       web-owned SQLite, cs71-web:cs71-web 0700 directory, 0600 file
/var/lib/cs71-vision/vision.db the self-labeled dataset (PI-VISION-002), cs71-vision:cs71-api 0700 directory, 0600 file
/var/lib/cs71-backups/<stamp>/ one backup.sh snapshot: all three databases, config and a manifest, root:root 0700
/run/cs71/cs71d.sock           systemd RuntimeDirectory=; recreated on every start, not persisted
/run/cs71-vision/cs71vision.sock  cs71-vision's own read-only dataset api (PI-VISION-003); systemd RuntimeDirectory=, recreated on every start, not persisted
```

**Three service-token files, not one.** `cs71d`, `cs71-web` and
`cs71-vision` are separate, mutually distrusting identities that happen to
need to agree on one shared secret: the daemon reads its own copy to know
what to accept (`cs71d.device`'s `service_token_path`), the web and vision
services each read their own copy to know what to present
(`CS71_WEB_SERVICE_TOKEN_PATH`, `cs71vision.config`'s
`daemon_service_token_path`). `deployment-and-operations.md`'s layout table
only shows the web's copy; this is the place that says there are three.

**A fourth, distinct credential pair for the machine actor kind**
(PI-VISION-007, ADR-0013) - `/etc/cs71d/machine-service-token` and
`/etc/cs71-vision/machine-service-token`, written by
`write_machine_service_token` (`lib/common.sh`), unconditionally, the same
way `write_service_tokens` always runs. This is a genuinely *different*
secret from the shared one above, not a fourth copy of it: `cs71-web` never
receives this file at all, since it has no legitimate reason to ever act as
the machine actor kind, and the daemon checks the actual credential
presented against the actual role claimed (see `appliance/daemon/README.md`'s
own "The machine actor kind" section) rather than trusting a request body's
say-so. The file is always provisioned, but `cs71d.toml` does not enable
the capability by default (`machine_service_token_path` is unset in
`production.example.toml`) - the file existing is not the same thing as the
capability being active, and nothing presents this credential until
PI-VISION-008 configures `cs71-vision` to.

**`/etc/cs71/cs71d.toml`, `/etc/cs71/cs71vision.toml` and `/etc/cs71/web.env`
are root-owned and world-readable**, deliberately. Every value in them is
already fixed by the production profile (`config.py`/`config.ts` refuse
anything else) or is not secret in the first place (a LAN hostname, a port
number). The credential lives in the three files above instead, each mode
0600 and readable only by matching UID — not by group membership, so it
does not depend on which supplementary groups a service ends up with.

## Users and groups

| Identity | Owns | Access to the serial device | Access to the socket(s) |
| --- | --- | --- | --- |
| `cs71d` (user) | `/var/lib/cs71d`, `/etc/cs71d/service-token` | Yes — the only one | Creates `cs71d`'s |
| `cs71-web` (user) | `/var/lib/cs71-web`, `/etc/cs71-web/service-token` | No | Reaches `cs71d`'s and `cs71-vision`'s via the `cs71-api` group |
| `cs71-vision` (user) | `/var/lib/cs71-vision`, `/etc/cs71-vision/service-token`, `/dev/cs71vision` | No | Creates its own (PI-VISION-003) and reaches `cs71d`'s, both via the `cs71-api` group |
| `cs71-api` (group) | nothing | — | `cs71d.service` and `cs71-vision.service` each run with this as their *effective* group, so the socket each creates comes out `<owner>:cs71-api` 0660; `cs71-web.service` carries it as a supplementary group to reach both |

`cs71d.service` sets `Group=cs71-api` (not its own `cs71d` group) specifically
so the socket comes out group-owned `cs71-api`; everything else the daemon
process creates (`machine.db`) is separately protected by its own 0600 file
mode (`journal.py` sets this explicitly), so it does not depend on which
group owns it.

## Systemd units

`systemd/cs71d.service` and `systemd/cs71-web.service` share one design:
`NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`, an empty
`CapabilityBoundingSet=`, and `RestrictAddressFamilies=` narrowed to exactly
what each needs — `AF_UNIX` only for the daemon (it has no code path that
ever opens an internet socket; `appliance/daemon/tests/test_api.py` asserts
that statically, and `tests/smoke-test.sh` proves the sandbox itself refuses
one at run time), `AF_UNIX AF_INET AF_INET6` for the web service (loopback
HTTP plus the daemon socket). Only `cs71d.service` declares
`DeviceAllow=/dev/cs71 rw`; the web unit has no device access at all.

`cs71d.service` only starts once the real adapter is present:
`BindsTo=dev-cs71.device` plus `ConditionPathExists=/dev/cs71` (the device
unit systemd auto-generates from the udev-tagged symlink below). There is no
way to functionally exercise that specific gate without the real USB
adapter, which is exactly why it is gated — `tests/smoke-test.sh` runs the
same sandbox directives against a derived unit with that gate removed and a
stub device file instead, on the simulator backend, so the rest of the
configuration is provably real without needing the hardware this backlog
item explicitly does not have (PI-OPS-001: "Hardware required: Pi required;
controller optional" — the controller is the part still missing).

`systemd/caddy-cs71.conf` is a drop-in for the *distribution's own*
`caddy.service`, not a replacement unit — Caddy already ships its own
hardened unit; this only adds `After=`/`Wants=cs71-web.service` so it starts
once SvelteKit is up.

`systemd/cs71-vision.service` (PI-VISION-001/002/003, ADR-0013) follows the
same design: `RestrictAddressFamilies=AF_UNIX` (it reaches `cs71d`'s socket to
read operation outcomes, PI-VISION-002, and serves its own dataset api,
PI-VISION-003 — nothing more, so no `AF_INET`/`AF_INET6` the way `cs71-web`
needs for its loopback port), and `DeviceAllow=/dev/cs71vision rw` is its own
camera symlink, disjoint from the controller's. Its *effective* `Group=` is
`cs71-api` — not a `SupplementaryGroups=` grant, but its own primary group at
runtime, the same shape `cs71d.service` itself uses (`User and groups`,
above): that one directive both lets it reach `cs71d`'s socket as a client
and makes its own dataset api socket come out `cs71-vision:cs71-api`, so
`cs71-web` can reach it the same way it already reaches `cs71d`'s.
`StateDirectory=cs71-vision` is where `vision.db` lives;
`RuntimeDirectory=cs71-vision` is where the dataset api socket lives,
recreated fresh on every start like `cs71d.service`'s own `RuntimeDirectory=`.
It gates capture on its own camera the same way `cs71d.service` gates on the
controller: `BindsTo=dev-cs71vision.device` plus
`ConditionPathExists=/dev/cs71vision` — the dataset api and correlation loop
share that same gate, since both are meaningless without the service running
at all. `tests/smoke-test.sh` runs the same derived-unit trick against the
fixture backend, since there is no real camera on the CI runner either — and,
once cs71d-smoke is up, points the derived unit's `daemon_socket_path` at
that real running instance and queries its own dataset api as `cs71-web`, so
the `cs71-api` group grant is proven for real in both directions, not just
declared.

## udev

`udev/99-cs71.rules` matches USB vendor ID, product ID **and serial
number** — not vendor/product alone, so it cannot pick up a different unit of
the same adapter model, and it does not match every generic USB-serial
device on the bus. No adapter has been approved yet
(`docs/architecture/deployment-and-operations.md`); the checked-in file has
`@@VENDOR_ID@@`/`@@PRODUCT_ID@@`/`@@SERIAL@@` placeholders that match
nothing until `install.sh` substitutes them from installer arguments — the
values are hardware evidence recorded during PI-HIL-001, not something this
repository invents. The rule tags the device `TAG+="systemd"` with
`ENV{SYSTEMD_WANTS}="cs71d.service"`, so the service starts the moment the
adapter is plugged in rather than racing boot-time unit ordering against a
path that might not exist yet.

`udev/98-cs71-vision.rules` matches the same way for the classifier camera,
on `SUBSYSTEM=="video4linux"` rather than `tty`, with one deliberate
difference: vendor ID and product ID only, no serial number. `99-cs71.rules`
needs a serial number because "any generic USB-serial adapter" is a broad
class it must not accidentally match; this rule matches one specific camera
module already chosen for this appliance
(`3DModels/Classifier/CameraV2`), where exactly one unit is ever expected to
be attached, and many inexpensive UVC modules do not reliably program a
unique USB serial string in the first place. `install.sh`'s
`--camera-vendor-id`/`--camera-product-id` are optional, unlike the
controller's identity arguments: omitting them installs and enables
`cs71-vision.service` with a rule that matches no real device yet, the same
graceful "nothing to do" state the checked-in file is always in before
substitution — existing `install.sh` callers (`tests/smoke-test.sh`
included) keep working unmodified.

## Caddy

`caddy/Caddyfile.cs71` is the entire site: one host block, proxying only to
`127.0.0.1:<port>`. There is no route to the daemon socket, the daemon API,
a static secret or a SQLite file in it, because there is no other route in
it at all. TLS is Caddy's own automatic certificate management, which falls
back to its internal CA for a hostname with no public DNS — the private-LAN
default this appliance assumes throughout
(`docs/architecture/vision-and-scope.md`). `flush_interval -1` is there
because the `/events` SSE stream needs each write flushed immediately, not
buffered.

## Installing

```sh
sudo appliance/ops/install.sh \
  --hostname cs71.local \
  --vendor-id 0403 --product-id 6001 --serial <adapter-serial> \
  --camera-vendor-id 0403 --camera-product-id 6002 \
  --web-port 3000 \
  --start
```

Run this from a checkout of the whole repository, on the target machine
itself — it builds the web workspace's native modules (`better-sqlite3`,
`@node-rs/argon2`) on that machine on purpose, so they match its actual
architecture rather than whatever built them. Without `--start` it installs
and enables all three services but does not start them, which is what the
`appliance-ops` CI job's static checks exercise (it never has a real
adapter or camera to start against anyway). The two `--camera-*` arguments
are optional, unlike the controller's own identity — see the udev section
above.

**`tsx` ships as a real `dependencies` entry, not a devDependency,
deliberately.** `install.sh` runs a full `npm ci` to get every build tool
SvelteKit needs, builds, then `npm prune --omit=dev` — everything except
`dependencies` is gone from the deployed `/opt/cs71/web/node_modules`
afterward. The one-time bootstrap-token CLI
(`appliance/web/scripts/issue-bootstrap-token.ts`) still has to run *after*
that prune, on the box, against the box's own fresh `web.db` — so whatever
runs it has to survive the prune too.

**The bootstrap CLI is the first thing to ever open `web.db`, deliberately.**
It runs, as the `cs71-web` identity, before either service starts — creating
and migrating the database itself — so the service that starts afterward
just opens what is already there instead of racing a second process to
migrate the same empty file.

## Backup, restore and upgrade

`backup.sh` takes a SQLite-consistent online backup of `machine.db`,
`web.db` and — when it exists — `vision.db`
(`sqlite3 ... .backup`, which needs neither service stopped) plus
`cs71d.toml`/`web.env`, into a timestamped directory under
`/var/lib/cs71-backups`, with a checksummed `manifest.json`
(`lib/manifest.py`) recording the source commit and each workspace's own
version — read from `/opt/cs71/ops/release-info.json`, which `install.sh` and
`upgrade.sh` write once at build time rather than `backup.sh` introspecting a
checkout that a periodic timer run has no guarantee is even still there.
`vision.db` is optional in both the backup and the manifest: an appliance
upgraded from a pre-PI-VISION install may not have one yet, and that must
never fail a backup that only ever needed `machine.db`/`web.db` to work.
No service-token file is ever included: a restored backup should bring
back data and configuration, never a credential, and a restore never needs
one — the credential files already on the box are untouched by it. Every
attempt, success or failure, writes exactly one
`/var/lib/cs71d/backup-status.json` — `{"ok": bool, "completed_at": ...}` —
which is the only thing `cs71d`'s own `BackupFreshnessMonitor`
(`appliance/daemon/src/cs71d/storage_health.py`) ever reads; the daemon never
runs a backup itself; it only refuses new work once that file says no
successful one has happened recently enough (see the section below).
`systemd/cs71-backup.timer` runs it daily; `install.sh` copies `backup.sh` and
`lib/manifest.py` to `/opt/cs71/ops` specifically so the timer keeps working
after the checkout that installed it moves, updates or is deleted —
`restore.sh` and `upgrade.sh` are not copied there, and are meant to be run
from an up-to-date checkout, the same way `install.sh` itself is.

`restore.sh --from <backup dir>` verifies the manifest's checksums and each
present database's `PRAGMA integrity_check` *before* touching anything live,
stops `cs71-web.service`/`cs71-vision.service` then `cs71d.service`, installs
the databases and configuration (`vision.db` too, when the backup has one),
then starts the daemon and proves it actually answers
(`GET /v1/health/live` through its own socket, with its own credential)
before starting the web service and proving the same
(`GET /login` on its loopback port) — the "restore is validated by integrity
check and application read-only smoke test" contract in
[data-and-persistence.md](../../docs/architecture/data-and-persistence.md).
`cs71-vision.service` is started too, best-effort: a fresh or camera-less
install never brings it up at all (`ConditionPathExists=/dev/cs71vision`),
and that absence is not a restore failure. When it does come up, its own
dataset api (PI-VISION-003) is queried the same way (`GET /v1/dataset`
through its socket) — logged as a warning rather than failing the restore,
since sorting does not depend on `cs71-vision`. Any daemon or web failure
stops the script rather than declaring success; the runbooks in
`deployment-and-operations.md` take over from there.

`upgrade.sh` backs up first, stops web and vision then daemon, rebuilds the
web workspace, the daemon venv and the vision venv from the current checkout
(the same `build_web_workspace`/`build_daemon_venv`/`build_vision_venv`
`install.sh` itself calls, in `lib/common.sh`, so none of the three ever
drift apart), then starts the daemon and the web service the same validated
way `restore.sh` does, and the vision service best-effort. There is no
separate migration-runner command: opening `machine.db`/`web.db`/`vision.db`
*is* the forward-only, checksummed migration, so "apply migrations" and
"start the daemon"/"start the web service" are the same step here, not two.
Any failure before the web service is confirmed healthy on the new build
rolls back: the previous `/opt/cs71/web`, `/opt/cs71/daemon` and (if it
existed before the upgrade) `/opt/cs71/vision` are swapped back in, and
`restore.sh` is called against the pre-upgrade backup to bring the data back
too — reusing its own stop → verify → install → start → smoke-test sequence
rather than a second copy of it. If the rollback itself fails, the script
says so loudly and stops; that is a "call a human" situation the deployment
runbook owns, not something a script should keep guessing at.

## The daemon's own durability monitor

`cs71d`'s production profile carries a `DurabilityMonitor`
(`appliance/daemon/src/cs71d/storage_health.py`,
`production_durability_monitor`) that two things beyond a failed journal
write can trip: free space beside `machine.db` falling under a fixed 500 MiB
floor, and `/var/lib/cs71d/backup-status.json` being absent, recording a
failed backup, or older than 48 hours (`cs71-backup.timer` runs daily, so
that is one missed run of slack). Either one latches the machine exactly the
way a failed journal write already does —
`journal_available` goes false, `/v1/health/ready` reports `ready: false`
with the reason, and new operations are refused — because both threaten the
same guarantee a journal write does: something admitted now might not be
durably recoverable. The check re-runs on every readiness poll and before
every admission, so the fault surfaces even when nothing is being submitted,
not only once a write is attempted. Like a journal fault, it does not clear
itself; remediating the disk or the backup and restarting `cs71d` is what a
technician does next. Development and test profiles carry no monitor at all —
their databases live in throwaway paths with no installed backup timer behind
them, so there is nothing meaningful to watch there.

## What `tests/smoke-test.sh` does and does not prove

It runs the real `install.sh`, then starts the real `cs71d`/`cs71-web`
services — as derived units with the udev/device gate stripped and a stub
`/dev/cs71` file in its place, on the simulator backend — under their
unmodified sandbox directives, and checks, for real: the socket comes out
`cs71d:cs71-api` mode `0660`; the `cs71-web` identity can reach it with its
own credential and get a real answer; that same identity cannot read the
serial-device stub; the Node process answers on its loopback port under
`ProtectSystem=strict` and the rest; and a process carrying the daemon's own
sandbox properties is kernel-refused when it tries to open an `AF_INET`
socket. This runs in CI on `ubuntu-latest` — a real Linux host, real systemd,
real users — not a mock of any of it.

It then runs a real `backup.sh` against the databases those services just
wrote, verifies the manifest and marker for real, and runs a real
`restore.sh` — through the daemon's actual, fixed unit names (`cs71d.service`,
`cs71-web.service`), not the derived `-smoke` ones, by pointing those real
names at the same simulator-backed unit content for the duration of the
drill and putting the originals back afterward. That lets `restore.sh` run
completely unmodified — the exact script and exact unit names a real restore
uses — while still only ever starting the simulator backend, for the same
DTR-gate reason the rest of this script does. `upgrade.sh`'s rebuild step is
not run here (it would just repeat `install.sh`'s own build under a slower
name); its ordering, its rollback path calling `restore.sh`, and its
backup-before-anything-else sequencing are checked structurally instead, in
`tests/test_artifacts.py`.

It also derives a `cs71-vision-smoke.service` the same way, on the fixture
camera backend, and checks it stays active and actually logs a captured
frame (not just "active" — `systemctl is-active` alone would not prove the
capture loop is really running). Its `daemon_socket_path` points at the
real, already-running `cs71d-smoke.service` from earlier in the script —
real proof `Group=cs71-api` and `RestrictAddressFamilies=AF_UNIX`
actually let it reach the daemon, not just that the fixture camera works in
isolation: the check confirms no "could not reach cs71d" error was logged
and that `vision.db` was actually created, `cs71-vision:cs71-api` owned,
proving `DatasetStore.open()` succeeded for real. It does not drive an
actual sort operation through `cs71d-smoke` to prove a labeled example gets
recorded end to end — `Correlator`/`DaemonClient` already have thorough
unit/integration coverage of that logic (`appliance/vision/tests/`,
including a real fake HTTP server over a real `AF_UNIX` socket), and this
smoke test's job is the sandbox/packaging layer underneath it, not a second
copy of that coverage.

**PI-VISION-003's own dataset api socket is checked the same way.** The
script waits for `/run/cs71-vision/cs71vision.sock` to appear (systemd's
`RuntimeDirectory=`), asserts it is `cs71-vision:cs71-api` 0660 - the same
shape `/run/cs71/cs71d.sock` already is - and then, running *as* `cs71-web`
with `cs71-web`'s own copy of the shared credential, issues a real
`GET /v1/dataset` through it and checks for `200`. That is real proof the
new `Group=cs71-api` grant lets `cs71-web` reach a socket `cs71-vision`
itself created, not only one `cs71d` created - the two are different claims,
and PI-VISION-002 only ever proved the first.

What it cannot prove, and does not claim to: that the production profile
(`backend = "serial"`) starts at all. It does not, on any Linux host,
Pi included — Linux DTR is `NOT_EXECUTED`
(`docs/architecture/security-and-safety.md`, SAF-07), so
`cs71d.device.create_transport_factory` refuses to open a real port outright.
That is not a smoke-test gap; it is the DTR gate doing exactly what it is
for. Closing it is PI-HIL-001, and needs the physical adapter, wiring and
instrumentation this backlog item was never scoped to have. The same is
true of `cs71-vision`'s `V4L2Camera`: nothing here proves it opens a real
camera correctly, only that its own refusal path and the fixture-backed
service packaging work. Real camera evidence is PI-HIL/hardware-evidence
territory, the same class of gap.

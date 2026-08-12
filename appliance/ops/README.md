# Appliance packaging: systemd, udev, Caddy

What `install.sh` builds on a Raspberry Pi (or any Debian-family Linux host,
for the smoke test): two least-privilege systemd services, a udev rule that
gives one of them exclusive access to one specific USB adapter, and a Caddy
site that fronts the other on the LAN. This mirrors
[deployment-and-operations.md](../../docs/architecture/deployment-and-operations.md)
and [ADR-0009](../../docs/architecture/adr/0009-native-systemd-udev-caddy.md);
where this document and that one differ on a detail, this one is the one the
installer actually does.

## Layout

```text
/opt/cs71/web/                 SvelteKit source + a fresh npm ci/build, pruned to dependencies
/opt/cs71/daemon/venv/         a dedicated Python venv with cs71_protocol + cs71d installed into it
/etc/cs71/cs71d.toml           daemon config — world-readable, nothing secret in it
/etc/cs71/web.env              web PORT/HOST/ORIGIN — world-readable, nothing secret in it
/etc/cs71d/service-token       the daemon's own copy of the shared credential, cs71d:cs71d 0600
/etc/cs71-web/service-token    the web's own copy of the same credential, cs71-web:cs71-web 0600
/var/lib/cs71d/machine.db      daemon-owned SQLite, cs71d:cs71d 0700 directory, 0600 file
/var/lib/cs71-web/web.db       web-owned SQLite, cs71-web:cs71-web 0700 directory, 0600 file
/run/cs71/cs71d.sock           systemd RuntimeDirectory=; recreated on every start, not persisted
```

**Two service-token files, not one.** `cs71d` and `cs71-web` are separate,
mutually distrusting identities that happen to need to agree on one shared
secret: the daemon reads its own copy to know what to accept
(`cs71d.device`'s `service_token_path`), the web reads its own copy to know
what to present (`CS71_WEB_SERVICE_TOKEN_PATH`). `deployment-and-operations.md`'s
layout table only shows the web's copy; this is the place that says there
are two.

**`/etc/cs71/cs71d.toml` and `/etc/cs71/web.env` are root-owned and
world-readable**, deliberately. Every value in them is already fixed by the
production profile (`config.py`/`config.ts` refuse anything else) or is not
secret in the first place (a LAN hostname, a port number). The credential
lives in the two files above instead, each mode 0600 and readable only by
matching UID — not by group membership, so it does not depend on which
supplementary groups a service ends up with.

## Users and groups

| Identity | Owns | Access to the serial device | Access to the socket |
| --- | --- | --- | --- |
| `cs71d` (user) | `/var/lib/cs71d`, `/etc/cs71d/service-token` | Yes — the only one | Creates it |
| `cs71-web` (user) | `/var/lib/cs71-web`, `/etc/cs71-web/service-token` | No | Via the `cs71-api` group |
| `cs71-api` (group) | nothing | — | `cs71d.service` runs with this as its effective group, so the socket it creates is `cs71d:cs71-api` 0660; `cs71-web.service` carries this as a supplementary group to reach it |

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
  --web-port 3000 \
  --start
```

Run this from a checkout of the whole repository, on the target machine
itself — it builds the web workspace's native modules (`better-sqlite3`,
`@node-rs/argon2`) on that machine on purpose, so they match its actual
architecture rather than whatever built them. Without `--start` it installs
and enables both services but does not start them, which is what the
`appliance-ops` CI job's static checks exercise (it never has a real
adapter to start against anyway).

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

What it cannot prove, and does not claim to: that the production profile
(`backend = "serial"`) starts at all. It does not, on any Linux host,
Pi included — Linux DTR is `NOT_EXECUTED`
(`docs/architecture/security-and-safety.md`, SAF-07), so
`cs71d.device.create_transport_factory` refuses to open a real port outright.
That is not a smoke-test gap; it is the DTR gate doing exactly what it is
for. Closing it is PI-HIL-001, and needs the physical adapter, wiring and
instrumentation this backlog item was never scoped to have.

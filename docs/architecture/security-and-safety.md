# Security and Safety

## Assets, threats and surfaces

| Asset | Primary threat | Control |
| --- | --- | --- |
| Machine motion and operator safety | unauthorized, stale or ambiguous command | RBAC, CSRF, generation checks, single serial owner, fail-closed `UNCERTAIN`, physical E-stop. |
| Serial device/session | competing owner, device substitution, malformed response | dedicated user/lock, udev stable identity, strict `cs71_protocol`, least privilege. |
| Local accounts/sessions | credential theft, fixation, CSRF | Argon2id, opaque rotating sessions, secure cookies, CSRF tokens/origin checks, rate limits. |
| Daemon API | LAN/browser exposure or confused deputy | Unix domain socket only, socket group/modes, BFF identity, Caddy never proxies it. |
| Journals/configuration | tampering, data loss, false completion | separate SQLite ownership, permissions, backups, migration checks, journal failure blocks motion. |
| Release artifacts | dependency/supply-chain compromise | pinned/reviewed dependencies, SBOM/license/vulnerability CI, signed/provenanced release process when adopted. |

Attack surfaces are HTTPS forms/SSE, Caddy configuration, Node dependency tree, Unix socket, service environment/configuration, USB device and firmware serial frames, local console, backups and upgrade artifacts. The appliance is designed for a trusted private LAN, not as an Internet-exposed service.

## RBAC matrix

| Capability | Viewer | Operator | Administrator |
| --- | :---: | :---: | :---: |
| Read snapshot/history/faults | yes | yes | yes |
| Software stop | yes | yes | yes |
| Connect, home, sort, feed | no | yes | yes |
| Recovery/reset | no | no | yes, explicit confirmation |
| Change permitted configuration | no | no | yes |
| Manage users/provisioning | no | no | yes |
| Direct protocol command/device path | no | no | no |

SvelteKit enforces these rules server-side before calling `cs71d`; browser components are not an authority. The daemon accepts only its BFF service identity and restricted actor attribution—not arbitrary browser credentials or roles.

## Authentication and session protections

Initial provisioning is local-console/installer mediated and creates a single administrator through a one-time, expiry-bound mechanism; no default password ships. Password hashes use Argon2id with current calibrated parameters. Cookies are `Secure`, `HttpOnly`, `SameSite` appropriate to the single origin, scoped narrowly, rotated after login/privilege change and revoked on logout/password disable. State-changing requests require per-session CSRF validation plus Origin/Referer policy. Login, session and command routes have request-body, rate and concurrency limits. Audit logs redact credentials, cookies, CSRF values and secrets.

## Unix socket and service hardening

The socket lives in `/run/cs71/cs71d.sock`, owned by `cs71d` with a dedicated group readable/connectable only by the SvelteKit service user; no TCP listener is created. `cs71d` has access to the configured serial device and its state directory only. SvelteKit can connect to the socket but cannot open the serial device or `machine.db`. Use separate unprivileged users, restrictive `umask`, systemd credentials/environment files with root ownership, `NoNewPrivileges=yes`, private temporary directories, read-only system paths where practical, restricted address families, capability bounding, device allow-lists, memory/CPU limits and syscall protection after compatibility testing. Hardening must not conceal required USB access or journal faults.

Caddy terminates TLS and proxies only to loopback SvelteKit. Production/pilot certificates, hostname and TLS policy are explicitly configured; development may use plain loopback/LAN only under documented profile restrictions. Secrets are generated per installation, never committed, redacted in logs and rotated by documented procedure.

## Safety invariants

| ID | Invariant |
| --- | --- |
| SAF-01 | Only the serial worker in `cs71d` accesses the controller serial session. |
| SAF-02 | No state-changing operation is successful without a trusted, correlated firmware terminal. |
| SAF-03 | Timeout, USB loss, malformed/CRC/interleaved protocol response, journal failure or failed recovery cannot become success; affected state is `UNCERTAIN` or failed. |
| SAF-04 | Priority software stop bypasses ordinary command admission, but software stop is never labelled or relied on as E-stop. |
| SAF-05 | Snapshot-generation mismatch rejects motion before serial I/O; browser/SSE retry never replays an action without idempotency semantics. |
| SAF-06 | Node/SvelteKit availability is not a serial ownership dependency; its restart does not interrupt an active daemon operation. |
| SAF-07 | POSIX/Linux DTR behavior is **NOT_EXECUTED** and unqualified until physical experiment evidence closes the gate. |
| SAF-08 | Simulator evidence cannot satisfy any hardware, DTR, physical-stop or production-release gate. |

Unsafe or uncertain behavior is explicit: a disconnect or non-correlatable result disables dependent actions, prominently displays `UNCERTAIN`, preserves evidence, and requires the authorized recovery procedure. “Ready” is not a claim of physical clearance, homing, energy isolation, or E-stop availability. A physical emergency stop and guarded motor-power path are mandatory operational controls outside the software trust boundary.

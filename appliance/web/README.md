# CS7.1 appliance web

SvelteKit SSR/Node.js workspace for the browser-facing BFF and operator UI. The
browser will communicate only with this service; direct daemon and serial access
are outside this workspace's boundary.

Use Node.js 22.12 or newer and npm 10.9 or newer; `.nvmrc` selects the
CI-tested Node 22 patch release.

## Developing

Install the locked dependencies and start a development server:

```sh
npm ci
npm run dev
```

## Building

Type-check and create the Node SSR production bundle:

```sh
npm run check:api
npm run lint
npm run check
npm test
npm run build
```

`check:api` verifies that committed TypeScript definitions match the canonical
`cs71d` OpenAPI document.

## Authentication

`src/lib/server/auth/` holds the server-side authentication core. Everything in
it runs only on the server: SvelteKit never bundles `$lib/server` into client
code, so no hashing parameter, session row or token digest can reach the
browser.

| Module             | Responsibility                                                                     |
| ------------------ | ---------------------------------------------------------------------------------- |
| `database.ts`      | The only module that opens `web.db` or writes SQL; migrations and file permissions |
| `passwords.ts`     | The only module that imports `@node-rs/argon2`; the Argon2id policy                |
| `tokens.ts`        | Opaque 256-bit credentials, their digests and non-secret identifiers               |
| `users.ts`         | Local accounts, authentication, password change and disable                        |
| `sessions.ts`      | Opaque server-side sessions, their two expiry bounds and revocation                |
| `provisioning.ts`  | One-time, expiry-bound bootstrap of the first administrator                        |
| `capabilities.ts`  | The documented RBAC matrix as data; the only place a role becomes permission       |
| `policy.ts`        | What each route requires, keyed by route id                                        |
| `authorization.ts` | Turning a policy or a capability into a refusal                                    |
| `csrf.ts`          | The Origin rule and the token a state-changing request must echo back              |

`src/lib/server/limits.ts` sits beside them and holds the rate and concurrency
budgets, which ration cost rather than decide identity.

`web.db` is this workspace's own database and is never the daemon's
`machine.db`. It is created owner-only and refuses to open when another local
identity can already read it. Its migrations are forward-only and checksummed,
so a newer or altered schema stops the service rather than being downgraded in
place.

No default account and no default password ship. A fresh appliance has an empty
`users` table, so the first administrator can only be created by claiming a
one-time bootstrap token before it expires. Issuing a token supersedes any
outstanding one, and provisioning cannot be re-opened once claimed.

Passwords are stored only as Argon2id encodings, and session and bootstrap
tokens only as SHA-256 digests of 256-bit random values, so a stolen copy of
`web.db` yields no usable credential — a test asserts this against the raw file
bytes. Logging in issues a new session and revokes the one it replaces; logout,
idle expiry, absolute expiry, account disable and password change all revoke.

The Argon2id parameters were measured at roughly 100 ms per hash on an arm64
development machine. That is **not** Raspberry Pi evidence; the cost on the
appliance is unmeasured until it is timed on representative hardware.

### The request boundary

`src/hooks.server.ts` resolves the session on every request and then authorizes
it, both **denying by default**: a route is reachable without a session only if
its policy says so, and a signed-in account reaches it only if the policy names
a capability the role holds, so a page added later is protected by omission
rather than exposed by it. A request presenting a token the server will not
honour always leaves without that cookie.

The session cookie carries an opaque token and nothing else — no role, no user
id, no signed claims. It is `HttpOnly`, `SameSite=Strict` and root-scoped in
every profile, and `Secure` with the `__Host-` prefix in production, which
browsers accept only for a cookie that is secure, root-scoped and carries no
`Domain`.

The login redirect carries a fixed reason code and never a caller-supplied
return path, so the login page cannot be turned into an open redirect. Signing
out is a POST; a `GET /logout` would let any page on the network sign an
operator out of a running machine.

### Authorization

Roles become permission in exactly one place. `capabilities.ts` transcribes the
RBAC matrix from `docs/architecture/security-and-safety.md`, and routes ask for
a capability rather than for a role, so the next change to that table is one
edit rather than a search for every handler that compared a role name.

Two rows of the matrix are worth repeating here. A viewer may stop the machine:
withholding the software stop from the least privileged account would make an
access-control table into a safety decision. And no role at all may drive the
protocol or the device path, so `protocol.direct` exists in order to be refused
— for an administrator too.

`policy.ts` declares what each route requires, keyed by SvelteKit route id, and
the hook consults it before any page or action runs. A route with no entry is
**refused**, and `policy.spec.ts` scans `src/routes` so a page added without an
entry fails the build rather than waiting to be discovered in production. A
path that matched no route is left alone to answer as missing; turning every
wrong URL into a permission error would only confuse the person reading the
screen.

The route table is the coarse gate. A route whose actions differ in privilege
calls `requireCapability` inside the action as well: an action is where
privilege is spent, and the check that matters is the one next to the effect.
Pages are told what the role may do so they can show the right controls, but
that list is a reflection of the server's decision, never a substitute for it.

### Forgery and cost

A state-changing request — anything but `GET`, `HEAD` or `OPTIONS` — is checked
before it reaches a handler, cheapest check first, so a forged or oversized
request costs this appliance a header comparison rather than a database read or
an Argon2id hash.

Two independent controls stand against cross-site request forgery. The request
must name this appliance's own origin; a missing `Origin` header is refused
rather than excused, because browsers have sent it on state-changing requests
for years and an exemption for "no Origin" is an exemption an attacker can ask
for. And it must echo back a token that a forging page cannot read, in the
`csrf_token` field or the `X-CSRF-Token` header.

For a signed-in browser that token is derived from the session token by HMAC,
so there is nothing extra to store: the server already receives the session
token in the cookie on every request, the derivation is one way, the token
rotates with the session, and a stolen `web.db` still yields nothing because the
database holds only the session token's digest. A browser with no session gets a
random token in an `HttpOnly`, `SameSite=Strict` cookie of its own, which the
login form echoes back — signing in is state-changing too, since it costs a
password hash and a forged one would leave an operator working inside somebody
else's account.

The budgets in `limits.ts` ration what a stranger on the network can spend:

| Control                   | Budget                                    |
| ------------------------- | ----------------------------------------- |
| State-changing requests   | 30 per minute per client address          |
| Sign-in attempts          | 5 per minute, per account and per address |
| Password hashes in flight | 2 at once, refused rather than queued     |
| Declared form body        | 16 KiB                                    |

Succeeding clears the sign-in budget, so an operator who mistyped once is not
still being punished a minute later. The concurrency limit refuses rather than
queues because an Argon2id hash costs 64 MiB of working memory: a queue would
turn a burst into memory pressure plus a delay, and the person waiting cannot
tell that apart from a machine that has stopped answering. The body check reads
the declared `Content-Length` as an early, cheap refusal; the enforcement that
cannot be lied to is the adapter's own `BODY_SIZE_LIMIT`, which counts bytes as
they arrive.

There is no operator-facing command to print a bootstrap token until the
installer provides one, so a fresh appliance cannot yet be provisioned without
writing code.

## Talking to the daemon

`src/lib/server/daemon/` is the only way this workspace speaks to `cs71d`.

| Module           | Responsibility                                                          |
| ---------------- | ----------------------------------------------------------------------- |
| `client.ts`      | Named commands with typed arguments, and the headers the contract needs |
| `transport.ts`   | One HTTP exchange over the Unix domain socket                           |
| `credentials.ts` | Reading the service credential from its protected file                  |
| `errors.ts`      | What went wrong, and what the operator is told about it                 |

There is no host, no port and no URL in any of it: the socket path comes from
configuration and the request path is a literal the client builds, so a browser
has nothing to point somewhere else. The client exposes named operations —
`snapshot`, `connect`, `recover`, `stop`, `home`, `sort`, `feed`,
`configuration`, `updateConfiguration`, `operation` — and no method that takes a
path, a device or a protocol string. Arguments are validated before anything is
sent, so an out-of-range slot fails here rather than as a daemon rejection.

Types come from the generated contract, so a change to `cs71d-v1.openapi.json`
this code has not caught up with fails the build rather than a machine.

The credential is read from the file named by `CS71_WEB_SERVICE_TOKEN_PATH`
(pinned to `/etc/cs71-web/service-token` in production), never from an
environment value — the process table is readable by any local user. A file
other users can read is refused rather than repaired, on the same reasoning as
`web.db`. It is read once per process; rotation is a service restart.

Every command carries an idempotency key, a generation to match and a deadline.
Those are what stop a resubmitted form from moving the machine twice, a stale
page from acting on a machine that has changed, and a command from outliving the
operator's attention. The software stop matches any generation (`*`), which the
contract allows there and nowhere else: a stop refused because the page was a
few seconds old would be a stop that did not happen. It is still a software
stop and never an emergency stop.

An accepted command returns an `operation_id` and a pending state. It is never a
claim that the machine did anything.

Daemon errors are translated rather than forwarded. The server keeps the
daemon's code, request id and words for correlation; the browser gets a sentence
this workspace wrote. A daemon `401` or `403` means this service's own
credential is wrong, so the operator is told the service is unavailable and
never that their account was refused. `UNCERTAIN` says the machine may have
moved and that the recovery procedure comes first.

No route calls any of this yet — wiring the dashboard snapshot and the stop
action into server actions, with audit entries in `web.db`, is the rest of
PI-BFF-001.

Set `CS71_WEB_DATABASE_PATH` to move `web.db` in development; the production
profile pins it to `/var/lib/cs71-web/web.db`.

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

Per-session CSRF tokens and login rate limiting are the remainder of
PI-WEB-002 and are **not** implemented; SvelteKit's own rejection of
cross-origin form posts is the only CSRF control in place. There is no
operator-facing command to print a bootstrap token until the installer provides
one, so a fresh appliance cannot yet be provisioned without writing code.

Set `CS71_WEB_DATABASE_PATH` to move `web.db` in development; the production
profile pins it to `/var/lib/cs71-web/web.db`.

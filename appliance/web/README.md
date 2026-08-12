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

| Module            | Responsibility                                                                     |
| ----------------- | ---------------------------------------------------------------------------------- |
| `database.ts`     | The only module that opens `web.db` or writes SQL; migrations and file permissions |
| `passwords.ts`    | The only module that imports `@node-rs/argon2`; the Argon2id policy                |
| `tokens.ts`       | Opaque 256-bit credentials, their digests and non-secret identifiers               |
| `users.ts`        | Local accounts, authentication, password change and disable                        |
| `sessions.ts`     | Opaque server-side sessions, their two expiry bounds and revocation                |
| `provisioning.ts` | One-time, expiry-bound bootstrap of the first administrator                        |

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

The browser-facing boundary — the session cookie, the request hook and the
login and logout routes — is not implemented yet, and neither is role
enforcement. There is no operator-facing command to print a bootstrap token
until the installer provides one.

Set `CS71_WEB_DATABASE_PATH` to move `web.db` in development; the production
profile pins it to `/var/lib/cs71-web/web.db`.

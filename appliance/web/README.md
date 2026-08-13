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

`scripts/issue-bootstrap-token.ts` (`npm run bootstrap-token`, or
`appliance/ops/install.sh --start`, which invokes it as the `cs71-web`
identity) is the operator-facing command: it opens `web.db` directly — the
same database the running service reads, not a copy — calls
`issueBootstrapToken`, and prints the token alone on stdout so it can be
captured cleanly, with everything a human needs to read on stderr instead.
There is deliberately no route that does this: a fresh admin credential
reachable over the network, even loopback-only, would be a wider door than
the local shell already is. It runs via `tsx`
(`appliance/web/scripts/issue-bootstrap-token.spec.ts` proves the entry
point itself — the database path, a clean one-line message rather than a
stack trace, the exit code — as a real subprocess, since `issueBootstrapToken`
itself is already covered by `provisioning.spec.ts`), which is a
`dependencies` entry rather than a devDependency for exactly this reason: the
installer prunes devDependencies from the deployed bundle, and this script
has to keep working afterward.

## Talking to the daemon

`src/lib/server/daemon/` is the only way this workspace speaks to `cs71d`.

| Module           | Responsibility                                                          |
| ---------------- | ----------------------------------------------------------------------- |
| `client.ts`      | Named commands with typed arguments, and the headers the contract needs |
| `transport.ts`   | One HTTP exchange over the Unix domain socket                           |
| `credentials.ts` | Reading the service credential from its protected file                  |
| `errors.ts`      | What went wrong, and what the operator is told about it                 |
| `events.ts`      | Reading the daemon's event stream, and staying attached to it           |
| `broadcast.ts`   | One reader of that stream, fanned out to however many browsers watch    |

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

### Commands from a page

The dashboard reads the snapshot in its `load` and submits the software stop as
a named action. A daemon that is not answering makes the page report that
rather than fail: an operator whose machine has gone quiet still needs the
screen, and the stop control has to stay on it.

Authorization happens in the action, next to the effect, rather than being
inferred from the fact that a page rendered a button. The stop asks for
`machine.stop`, which a viewer holds.

The stop never reuses an idempotency key. Deduplication is for a resubmitted
intent; a second press of stop is a second intent, and a replayed key would let
the daemon answer with the first result and turn a stop into a no-op.

What comes back is an acceptance: an `operation_id` and a pending state, shown
as exactly that. The page says it is not a completion, and never calls a
software stop an emergency stop.

### The web audit

Every command writes a `web_audit` row in `web.db`, whatever the answer was —
accepted, refused, or never answered. An audit that recorded only the successes
would describe a machine nobody ever argued with.

| Column                             | Purpose                                          |
| ---------------------------------- | ------------------------------------------------ |
| `user_id`, `role`                  | who asked                                        |
| `action`                           | what they asked for, in this workspace's words   |
| `request_id`                       | this service's id for the browser request        |
| `operation_id`                     | the daemon's identity for the work, the join key |
| `daemon_code`, `daemon_request_id` | why it was refused, for correlation              |

This is attribution, not the machine's journal of record: `cs71d` owns what the
machine did, in its own database, and the two are never written in one
transaction. Entries cannot be edited — a trigger refuses the update — and the
table has no column for a password, a token or a form body, so redaction is a
property of the shape rather than a step somebody has to remember. There is no
foreign key to `users`, because an audit entry has to outlive the account it
describes.

The remaining commands — connect, home, sort, feed, recovery and configuration
— have clients but no screens; those are PI-UI-001.

### The event stream

`events.ts` is the only place that reconnects to `cs71d` on its own. That is
safe here and nowhere else, because reading is the only thing that can be
retried without asking whether the machine already did it: a command that timed
out may have moved the machine, and nothing in this workspace resends one. A
reconnection re-attaches a reader; the daemon owns the operation and keeps
running it whether or not this service is listening.

Resumption is honest about gaps. Each event carries the daemon's monotonic
`event_id`; a reconnection asks to continue after the last one this process
actually delivered, and the daemon answers either with what came next or with
`snapshot.required` when that cursor is too old. Both cases surface as a
`resync` message, and so does every reconnection even when the cursor was
honoured — a browser that redraws from a snapshot after a gap is correct where
one that keeps applying increments is guessing. A cursor the daemon has rejected
is forgotten rather than presented again, which would loop.

Nothing here interprets an event. The `event_id`, `operation_id` and
`generation` a browser will be shown are the daemon's own, unrenumbered: a
bridge that invented its own sequence would make the two ends impossible to line
up when something goes wrong. An event type this build does not know is passed
through, because ignoring it is the consumer's decision under the v1 contract,
not something to lose in transit. A single event is size-capped, and silence
past the idle window counts as a disconnection — the daemon sends heartbeats
precisely so a quiet stream can be told from a dead socket.

### One reader, many browsers

`broadcast.ts` attaches to the daemon's stream when the first browser subscribes
and lets go when the last one leaves. A second connection would buy nothing —
the events are identical for every viewer — and it would spend the daemon's
retention budget on a stream nobody is reading any faster. It also puts the cost
of a slow browser here, where dropping one is cheap.

A browser never applies back pressure. If it falls far enough behind that its
backlog is worthless, the backlog is discarded and it is told to read a
snapshot: more events cannot catch up a viewer that is that far out of date,
only a fresh snapshot can. Nothing in the fan-out ever waits for a browser, so
one that stops reading, or vanishes without closing, cannot slow the daemon's
event production or the serial worker behind it.

Recent events are retained so a browser whose connection blinked can resume
exactly where it was. That is not durable history and not a substitute for the
daemon's own retention: a cursor older than what is held, or from a position
this process never saw, is answered with a resynchronisation rather than a
guess.

### The browser's stream

`GET /events` is that fan-out as SSE. It requires `machine.read` — watching is
reading — and asks for it in the handler, next to the effect.

The browser's own `EventSource` is the reconnection policy. It re-sends the last
`id:` it saw, which is the daemon's `event_id` unrenumbered, so a resumption
names a position the daemon would understand. Only real events carry an `id:`; a
`resync` or `unavailable` notice does not, because a notice that moved the
cursor would make the next reconnection ask for a position that never existed.
A cursor this process cannot resume — absent, unparseable, too old, or from
before a restart — opens the stream with a `resync`.

The `event:` name is written raw, so it is stripped of anything that could end
the field: a daemon that has gone wrong must not be able to compose a second
frame inside the first. A comment frame goes out periodically so the reverse
proxy in front of the appliance does not close a connection that is quiet
because the machine is.

### What the browser does with it

`src/lib/machine-view.svelte.ts` holds the rule, and it is deliberately small:
the stream is a prompt, not a source of truth. Nothing there builds a machine
state out of an event. An event says the machine has moved past the generation
on screen, and the answer to that is to read a snapshot — the only thing that
describes the machine completely. So the screen is either a snapshot or a
snapshot being replaced, never a state assembled from whichever half of a
sequence arrived. Reading a snapshot is re-running the page's own server load,
so there is no second path to the machine to keep honest.

Reads are coalesced: a busy machine can produce events far faster than a page
can read, and one read per event would be load the page inflicted on itself.
What matters is that a read _ends_ after the last event. A screen that owes a
snapshot says so, so a stale picture is visibly stale rather than quietly wrong.

### What a screen may say

`src/lib/machine-status.ts` decides every sentence the dashboard shows about
the machine, as data rather than markup, which is what makes the rules
testable. Three rules run through it. Acceptance is not completion: no word of
completion (`COMPLETION_WORDS` is the pattern the specs enforce) may describe
an operation that has not settled, and settled is reached only through a
terminal the controller confirmed — `trusted_terminal` is the difference
between an outcome and a guess, so an unconfirmed terminal is presented as an
outcome that is not known rather than repeated by its state name. Not known is
worth saying: the wire has no third value for an axis the session never
observed, so a disconnected session reads "not known" rather than "not homed" —
an operator told "not known" homes the machine, which is safe; one told a guess
acts on it. `UNCERTAIN` carries its own tone, distinct from ordinary attention,
so a machine whose state is unknown cannot blend into one with a known problem.

The dashboard is composed from `src/lib/components/`: `MachineStatus.svelte`
renders those readings, and `StopControl.svelte` is deliberately first in the
document — and therefore first in the tab order, since nothing on the page
declares a positive `tabindex` — so a keyboard reaches the software stop before
anything else. `src/routes/rendered.ts` reads a server-rendered page the way a
keyboard does (focus order, accessible names, the form a control submits), and
`dashboard-page.spec.ts` uses it to drive the keyboard flow end to end: the
real load renders the page, the first focusable control is the stop, and
submitting exactly the fields its form carries reaches the fake daemon as a
stop command. It is still a software stop, and the page says so beside the
button.

### What a page may offer

`src/lib/machine-controls.ts` makes the same move for the manual controls that
`machine-status.ts` makes for readings: which of connect, home, sort and feed
may be offered — and the sentence beside a withheld one — is decided in one
tested module, from the snapshot the operator is looking at. No command form
exists at all when the machine has not been read, because a command names the
machine state it was decided against. Motion is withheld without a `READY`
session or when the daemon reports itself not ready, an axis the firmware does
not advertise is withheld by that name, and the sorter slot list is exactly
`slot_count` long. The feed control follows `feed_available` rather than a UI
opinion: today that means disabled, with the daemon's
`feed_unavailable_reason` shown verbatim, and it enables only if a qualified
firmware ever advertises feeding.

A command form is an intent, not a button press. `ManualControls.svelte`
renders each form with the snapshot generation the operator decided against
and an idempotency key the server load minted for that render, so the daemon
refuses a stale page with reload wording, and a resubmitted form is the same
command rather than a second one — the next render mints fresh keys for the
next intent. `manual-controls-page.spec.ts` proves it end to end: the real
load renders the page, and submitting exactly the fields the served home form
carries reaches the fake daemon with those values as its `If-Match-Generation`
and `Idempotency-Key` headers. Every attempt lands in `web_audit` under
`machine.connect|home|sort|feed`, including one this workspace's own form
checks refused before anything was sent.

### What the history remembers

`src/lib/operation-history.ts` is the same move again, this time over a page
of the daemon's durable record rather than the dashboard's single active
operation. It builds each row with `machine-status.ts`'s own
`operationReading` — the module is imported, not reimplemented — so the
history screen and the dashboard cannot describe the same operation two
different ways: an unsettled row is never worded as a completion, and a
terminal without `trusted_terminal` still reads as an outcome that is not
known. The `state` and `type` filters offer exactly the values the contract
defines; nothing here invents a grouping the daemon does not already have a
value for.

`/operations` is a `GET`, not a form post. Filtering and paging are query
string changes — `?state=SUCCEEDED&type=SORT&cursor=…` — so a bookmarked or
shared URL reproduces the same page, and there is no action here for the web
audit to record: reading history is the same kind of read the dashboard's
snapshot already is. A filter value this workspace does not recognise is
dropped in the load rather than forwarded to the daemon, so a stale or
mistyped query string shows the unfiltered page instead of failing the
screen, and paging forward carries the daemon's own opaque `next_cursor`.
`operation-history-page.spec.ts` proves the query string a request carries is
what the fake daemon actually receives, and that an unrecognised filter value
never reaches it.

### A machine whose state is not known

`+layout.svelte` styles `[data-tone="uncertain"]` more loudly than
`[data-tone="attention"]` — bold, bordered and filled rather than color alone
— so the two cannot collapse into the same visual weight. Which tone a
reading carries is still decided entirely in `machine-status.ts`; this is the
one place markup renders that decision, and it renders every tone the same
way everywhere `data-tone` appears, dashboard or system view.

### Recovery, with explicit confirmation

Recovery is a different capability from a manual command —
`machine.recover`, administrator-only — and `recoveryPlan()` in
`machine-controls.ts` decides it independently of `controlsPlan()`: it is
offered whenever a session is not already being established or already
recovering, which includes `UNCERTAIN` — recovery is the way back from
that — and a deliberate reset of an otherwise healthy session otherwise.
`RecoveryControl.svelte` renders the same protected-intent shape as a manual
command (snapshot generation, render-minted idempotency key) plus a required
`confirm` checkbox; the server checks that field before calling
`daemon.recover`, so a request that arrived without it is refused as
invalid rather than treated as consent. `recovery-page.spec.ts` proves this
end to end, including that an unchecked confirmation reaches the action as
nothing — `routes/rendered.ts`'s form reader was extended so a checkbox or
radio only counts as submitted when it carries `checked`, the same as a
browser. Every attempt lands in `web_audit` under `machine.recover`.

### What the system view reports

`src/lib/system-view.ts` decides what `/system` may say about facts that
are not per-session. Firmware version and journal health both come from the
snapshot the dashboard already reads — journal health is `machine-status.ts`'s
own `faultSummary`, captioned as inferred from recorded faults rather than a
dedicated check the daemon does not perform. Storage health has no daemon
data source at all yet, so the page says "Not reported by this service"
rather than inventing one. DTR-gate status comes from a new
`GET /v1/system`, added to the contract and to `cs71d` in the same slice: it
serializes the daemon's existing `DTR_GATE_STATUS` constant, and this module
never presents `NOT_EXECUTED`, or any value it has not seen before, as a
pass — an unrecognised value renders with the `uncertain` tone rather than
assumed safe. `system-page.spec.ts` proves the load reads both the snapshot
and the system facts through the real hook.

### What the dataset view reports

`/dataset` (PI-VISION-003/004/005) is a second, independent boundary from
the dashboard: it talks to `cs71-vision`, not `cs71d`.
`src/lib/server/vision/client.ts` is a small hand-typed client, not a
second generated OpenAPI contract; that weight was judged disproportionate
to `cs71-vision`'s api surface (see PI-VISION-003's backlog entry for the
full reasoning). It reuses `daemon/transport.ts#exchange` and
`daemon/credentials.ts#readServiceToken` directly, since both are generic
Unix-socket-HTTP infrastructure with nothing daemon-specific about them, and
authenticates with this service's own existing copy of the shared service
credential (`CS71_WEB_SERVICE_TOKEN_PATH`) — `cs71-vision` accepts the same
shared secret `cs71d` does, so no second credential file was introduced.
Failures are re-wrapped into this module's own `VisionError` rather than
left as a `DaemonError`, so nothing named "Daemon" leaks into a caller
talking to a different service.

`src/lib/dataset-view.ts` is the wording-as-data module: each class becomes
a `label`/`detail`/`tone` (`system-view.ts`'s own `Reading` shape), and a
class below the configured floor states why in its `detail` rather than
being omitted from the list. `modelReadings()` builds each recorded
candidate's per-class accuracy comparison against whichever model is
currently active — the page renders that table directly above a
candidate's own activate control, which is what satisfies ADR-0013's
"activation is refused unless the operator has been shown the candidate's
accuracy alongside the currently active model's": it is a property of what
the page renders, not a separate confirmation step.

Training, activating and rolling back (PI-VISION-005) are three ordinary
form actions (`?/train`, `?/activate`, `?/rollback`) shaped exactly like the
dashboard's own manual commands — `requireCapability` next to the effect,
`recordAudit` on every outcome including a refusal — except with no
generation or idempotency key, since `cs71-vision` has no optimistic-
concurrency model to match: none of these three ever touch `cs71d` or
machine state. They gate on a new capability, `vision.train`
(`security-and-safety.md`), granted at the `operator` row rather than
reserved to `administrator` — a deliberate departure recorded in ADR-0013,
since retraining/activating a classifier is neither machine motion nor an
irreversible action the way `machine.recover`/`config.write` are.

`/dataset` also shows live suggestion accuracy (PI-VISION-006) - a third,
visibly distinct figure next to the per-candidate held-out accuracy table,
since the two answer different questions: held-out accuracy is measured on
a split of the training data, live accuracy is measured against real
operator decisions after activation. `dataset-view.ts#suggestionAccuracyDetail`
renders `0/0` as "nothing matched yet" rather than a bare, misleading `0%`.

The dashboard (`routes/+page.server.ts`, the main `/` screen) reads the
current suggestion too, directly above the existing manual sort form -
"before the operator picks a slot" means the screen they are already
looking at, not a new one. This read is independent of, and never gates,
the daemon-facing parts of that same load: a `cs71-vision` outage is caught
and logged, and the dashboard shows no suggestion, but the machine snapshot,
the stop control and every manual command keep working exactly as if
`cs71-vision` did not exist - sorting does not depend on it (ADR-0013).
`dashboard-page.spec.ts` proves this with a real stand-in `cs71-vision`
closed mid-test: the machine state still renders normally.

`dataset-page.spec.ts` proves the dataset/model/training half through the
real hook: the load reads `GET /v1/dataset`, `GET /v1/models` and
`GET /v1/suggestion-accuracy` against a real stand-in `cs71-vision` on a
real `AF_UNIX` socket, and each action reaches its own resource
(`POST /v1/train`, `POST /v1/models/{version}/activate`,
`POST /v1/rollback`) and lands a real `web_audit` row, accepted or refused.

Set `CS71_VISION_SOCKET_PATH` to point at a different `cs71-vision` socket in
development; the production profile pins it to
`/run/cs71-vision/cs71vision.sock`.

### Restarting this service

A restart drops a database handle and a reader. It reaches nothing else: `cs71d`
owns every operation in flight and goes on running it, which is why restarting
the web service can neither cancel nor duplicate one, and why nothing is resent
on the way back up. A browser that reconnects across a restart is told to read a
snapshot, because what this process had retained went with it and the cursor the
browser holds cannot be shown to join up with what follows.

Set `CS71_WEB_DATABASE_PATH` to move `web.db` in development; the production
profile pins it to `/var/lib/cs71-web/web.db`.

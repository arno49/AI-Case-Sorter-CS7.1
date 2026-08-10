# Data and Persistence

## Ownership and durability

`cs71d` exclusively opens `/var/lib/cs71d/machine.db`; SvelteKit exclusively opens `/var/lib/cs71-web/web.db`. Both use SQLite WAL with explicit migration tooling, ownership-preserving file permissions and bounded transactions. No process reads/writes the other database as a shortcut. `operation_id` and sanitized actor/correlation fields are the cross-database join keys.

A journal write required for operation admission, state transition or terminal outcome must succeed before that transition is reported as durable. If it cannot, `cs71d` emits/records the best available fault, rejects new state-changing operations with `JOURNAL_UNAVAILABLE`, and becomes not-ready. It must not silently continue serial control on an unjournaled success path.

## `machine.db` outlines

| Table | Ownership/use | Key fields |
| --- | --- | --- |
| `schema_migrations` | daemon migration ledger | version, applied_at, checksum |
| `operations` | immutable identity and lifecycle | operation_id, request fingerprint, state, generation, deadline, actor correlation, terminal/trusted flag |
| `operation_transitions` | append-only lifecycle audit | transition_id, operation_id, from_state, to_state, occurred_at, reason |
| `machine_events` | bounded/replayable daemon SSE source | event_id, type, generation, operation_id, payload, occurred_at |
| `faults` | active and historical faults | fault_id, state, code, source, opened/cleared_at |
| `session_attempts` | connect/recovery evidence | attempt_id, state, protocol mode, result, timestamps |
| `configuration_snapshots` | applied machine config history | config_id, generation, payload, source, created_at |
| `idempotency_records` | deduplication window | key, request fingerprint, operation_id, expires_at |

Protocol `request_id` may be recorded only as diagnostic, session-scoped metadata and must never be used as a cross-session key. Raw secret material and unbounded raw serial content are not persisted.

## `web.db` outlines

| Table | Ownership/use | Key fields |
| --- | --- | --- |
| `schema_migrations` | web migration ledger | version, applied_at, checksum |
| `users` | local accounts | user_id, username, password_hash, role, disabled_at |
| `sessions` | server-side session lifecycle | session_id, user_id, expiry, revoked_at, csrf_secret reference |
| `web_audit` | user-facing attribution | audit_id, user_id, action, operation_id, request_id, outcome, occurred_at |
| `preferences` | non-safety UI preferences | user_id, key, value, updated_at |
| `provisioning_state` | bootstrap administration state | initialized_at, version |

Passwords are Argon2id hashes; sessions use opaque random tokens, not personal data in signed browser payloads. See [security-and-safety.md](security-and-safety.md).

## Retention, migration and recovery

Retention values are configurable, documented defaults subject to storage sizing, and must preserve active operations, active faults, idempotency window, audit minimum and event replay window. Pruning is transactional, observable and never removes data referenced by an active operation. Size/disk thresholds are monitored; hard threshold blocks new motion before journal durability is at risk.

Migrations are forward-only, versioned and tested against a production-like copy. Upgrade takes a verified backup, stops writers in service order, runs each migration transactionally where SQLite permits, validates schema/health, then starts daemon before web. A failed migration restores the backup and previous compatible release; schema-changing downgrade is not attempted in place.

Backups use SQLite-consistent backup/online copy procedures, encrypt at rest if copied outside the appliance, include manifest/version/checksum, and are restored only into a stopped service with owner/mode checks. Restore is validated by integrity check and application read-only smoke test before production use. Loss of `web.db` does not grant access; local users require controlled re-provisioning. Loss/corruption of `machine.db` does not permit state inference: daemon starts not-ready/`UNCERTAIN` until session recovery and required operator procedure complete.

## Failure handling and audit correlation

Database busy, I/O, corruption, low-space, WAL checkpoint or backup failures generate structured logs and health status with daemon `event_id`/`operation_id` where available. A web audit entry is complementary, not the machine journal of record. Correlation uses `operation_id`, BFF `X-Request-ID`, daemon event IDs and timestamps; protocol request IDs are diagnostic only. No cross-database transaction is assumed.

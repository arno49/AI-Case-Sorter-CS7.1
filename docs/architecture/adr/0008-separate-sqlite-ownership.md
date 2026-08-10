# ADR-0008: Separate SQLite databases and ownership

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

Machine durability and web identity have different owners, permissions and outage behavior. Shared writes create coupling and transaction ambiguity.

## Decision

Use separate daemon-owned `machine.db` and web-owned `web.db` SQLite databases. Correlate through `operation_id`; do not share writes or rely on distributed transactions.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Separate SQLite stores | Selected: least privilege and independent failure handling. |
| One shared SQLite file | Rejected: cross-service writes and permissions weaken boundary. |
| External database | Rejected: operational dependency not needed for MVP. |

## Consequences

### Positive

- Clear authority and backup/recovery responsibilities.
- Web outage/data loss cannot rewrite machine journal.

### Negative

- Cross-service audit views join by correlation fields, not SQL joins.

## Implementation constraints

- Journal failure is surfaced and blocks new motion.
- Database migrations/backups are per service and owner.

## Validation and revisit triggers

- Fault-inject journal/disk failures and restore both stores.
- Revisit if multi-node durability need is evidenced.

## Links

- [Data](../data-and-persistence.md); [PI-DOMAIN-002](../backlog.md#pi-domain-002--implement-journal-failure-and-priority-stop-semantics); [PI-OPS-002](../backlog.md#pi-ops-002--implement-backup-upgrade-and-rollback-procedures).

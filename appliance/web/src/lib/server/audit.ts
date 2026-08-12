/**
 * Who asked the machine to do what, from a browser.
 *
 * This is attribution and nothing more. `cs71d` owns the record of what the
 * machine actually did, in its own database; these rows say which account
 * submitted which intent, and carry the `operation_id` that joins the two. The
 * two databases are never written in one transaction — the daemon's journal
 * must not depend on this service being up, and this service must not be able
 * to write the machine's history.
 *
 * An entry is written whatever the answer was. A refusal and an unreachable
 * daemon are both things an operator did, and an audit that recorded only the
 * successes would describe a machine nobody argued with.
 *
 * There is no field here for a password, a token or a form body, so redaction
 * is a property of the shape rather than a step somebody has to remember.
 */

import type { WebDatabase } from './auth/database';
import { createIdentifier } from './auth/tokens';
import type { Role } from './auth/users';

export type AuditOutcome =
	/** The daemon took the command and gave it an identity. */
	| 'accepted'
	/** The daemon answered, and said no. */
	| 'refused'
	/** No usable answer: unreachable, timed out, or not the contract's. */
	| 'failed';

export interface AuditIntent {
	readonly userId: string;
	readonly role: Role;
	/** What the operator asked for, in this workspace's words: `machine.stop`. */
	readonly action: string;
	readonly outcome: AuditOutcome;
	/** This service's own id for the browser request, for correlation. */
	readonly requestId: string;
	readonly operationId?: string | null;
	readonly daemonCode?: string | null;
	readonly daemonRequestId?: string | null;
}

export interface AuditRecord extends AuditIntent {
	readonly auditId: string;
	readonly occurredAt: string;
}

interface AuditRow {
	audit_id: string;
	occurred_at: string;
	user_id: string;
	role: Role;
	action: string;
	outcome: AuditOutcome;
	request_id: string;
	operation_id: string | null;
	daemon_code: string | null;
	daemon_request_id: string | null;
}

const COLUMNS =
	'audit_id, occurred_at, user_id, role, action, outcome, request_id,' +
	' operation_id, daemon_code, daemon_request_id';

export function recordAudit(database: WebDatabase, intent: AuditIntent, now: Date): AuditRecord {
	const auditId = createIdentifier('audit');
	const occurredAt = now.toISOString();
	database
		.prepare(`INSERT INTO web_audit (${COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`)
		.run(
			auditId,
			occurredAt,
			intent.userId,
			intent.role,
			intent.action,
			intent.outcome,
			intent.requestId,
			intent.operationId ?? null,
			intent.daemonCode ?? null,
			intent.daemonRequestId ?? null
		);
	return { ...intent, auditId, occurredAt };
}

/** Most recent first, which is the order anybody investigating reads in. */
export function recentAudit(database: WebDatabase, limit = 50): readonly AuditRecord[] {
	return database
		.prepare<[number], AuditRow>(
			`SELECT ${COLUMNS} FROM web_audit ORDER BY occurred_at DESC, rowid DESC LIMIT ?`
		)
		.all(limit)
		.map(toRecord);
}

export function auditForOperation(
	database: WebDatabase,
	operationId: string
): readonly AuditRecord[] {
	return database
		.prepare<[string], AuditRow>(
			`SELECT ${COLUMNS} FROM web_audit WHERE operation_id = ? ORDER BY occurred_at`
		)
		.all(operationId)
		.map(toRecord);
}

function toRecord(row: AuditRow): AuditRecord {
	return {
		auditId: row.audit_id,
		occurredAt: row.occurred_at,
		userId: row.user_id,
		role: row.role,
		action: row.action,
		outcome: row.outcome,
		requestId: row.request_id,
		operationId: row.operation_id,
		daemonCode: row.daemon_code,
		daemonRequestId: row.daemon_request_id
	};
}

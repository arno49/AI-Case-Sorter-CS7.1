/**
 * The web audit: who asked, for what, and what came back.
 */

import { describe, expect, it } from 'vitest';

import { auditForOperation, recentAudit, recordAudit, type AuditIntent } from './audit';
import { NOW, at, memoryDatabase } from './auth/harness';

const OPERATION = '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c';

function intent(overrides: Partial<AuditIntent> = {}): AuditIntent {
	return {
		userId: 'user_0123456789abcdef',
		role: 'operator',
		action: 'machine.stop',
		outcome: 'accepted',
		requestId: 'req_0123456789abcdef',
		operationId: OPERATION,
		...overrides
	};
}

describe('an audit entry', () => {
	it('records the actor, the request and the operation that joins the two databases', () => {
		const database = memoryDatabase();

		const written = recordAudit(database, intent(), NOW);

		expect(recentAudit(database)).toEqual([
			{
				...intent(),
				auditId: written.auditId,
				occurredAt: NOW.toISOString(),
				daemonCode: null,
				daemonRequestId: null
			}
		]);
	});

	it('records a refusal, with the daemon code and request id for correlation', () => {
		const database = memoryDatabase();

		recordAudit(
			database,
			intent({
				outcome: 'refused',
				operationId: null,
				daemonCode: 'STALE_GENERATION',
				daemonRequestId: 'a5b1c2d3-0000-4000-8000-000000000000'
			}),
			NOW
		);

		expect(recentAudit(database)[0]).toMatchObject({
			outcome: 'refused',
			operationId: null,
			daemonCode: 'STALE_GENERATION',
			daemonRequestId: 'a5b1c2d3-0000-4000-8000-000000000000'
		});
	});

	it('records an attempt that got no usable answer at all', () => {
		// An audit that held only the successes would describe a machine nobody
		// ever argued with.
		const database = memoryDatabase();

		recordAudit(database, intent({ outcome: 'failed', operationId: null }), NOW);

		expect(recentAudit(database)[0].outcome).toBe('failed');
	});

	it('cannot be edited once it is written', () => {
		const database = memoryDatabase();
		const written = recordAudit(database, intent(), NOW);

		expect(() =>
			database
				.prepare('UPDATE web_audit SET outcome = ? WHERE audit_id = ?')
				.run('accepted', written.auditId)
		).toThrow('an audit entry cannot be edited');
	});

	it('refuses an outcome that is not one of the three', () => {
		const database = memoryDatabase();

		expect(() => recordAudit(database, intent({ outcome: 'maybe' as never }), NOW)).toThrow();
	});

	it('outlives the account it describes, so it has no foreign key to one', () => {
		const database = memoryDatabase();

		// No `users` row exists for this id, and the write still succeeds.
		expect(() => recordAudit(database, intent({ userId: 'user_deleted' }), NOW)).not.toThrow();
	});
});

describe('reading the audit', () => {
	it('answers most recent first', () => {
		const database = memoryDatabase();
		recordAudit(database, intent({ action: 'first' }), NOW);
		recordAudit(database, intent({ action: 'second' }), at(1_000));

		expect(recentAudit(database).map((entry) => entry.action)).toEqual(['second', 'first']);
	});

	it('honours the limit it was given', () => {
		const database = memoryDatabase();
		for (let entry = 0; entry < 5; entry += 1) {
			recordAudit(database, intent(), at(entry));
		}

		expect(recentAudit(database, 2).length).toBe(2);
	});

	it('collects everything said about one operation, in the order it happened', () => {
		const database = memoryDatabase();
		recordAudit(database, intent({ action: 'machine.stop' }), NOW);
		recordAudit(database, intent({ action: 'machine.stop', operationId: 'another' }), at(1));

		expect(auditForOperation(database, OPERATION).map((entry) => entry.action)).toEqual([
			'machine.stop'
		]);
	});
});

import { describe, expect, it } from 'vitest';

import {
	historyPlan,
	stateLabel,
	STATE_OPTIONS,
	typeLabel,
	TYPE_OPTIONS
} from './operation-history';
import { COMPLETION_WORDS } from './machine-status';
import type { Operation, OperationPage } from './machine';

function operation(overrides: Partial<Operation> = {}): Operation {
	return {
		api_version: 'v1',
		operation_id: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
		type: 'SORT',
		state: 'RUNNING',
		actor: { user_id: 'u-1', role: 'operator' },
		created_at: '2026-08-11T12:00:00.000Z',
		deadline_at: '2026-08-11T12:00:05.000Z',
		generation: 41,
		trusted_terminal: false,
		...overrides
	} as Operation;
}

function page(items: readonly Operation[], nextCursor: string | null = null): OperationPage {
	return { api_version: 'v1', items, next_cursor: nextCursor };
}

describe('the option lists a filter may offer', () => {
	it('offers every state and type the contract defines, nothing invented', () => {
		expect(STATE_OPTIONS).toEqual([
			'QUEUED',
			'ACCEPTED',
			'RUNNING',
			'SUCCEEDED',
			'FAILED',
			'CANCELLED',
			'UNCERTAIN'
		]);
		expect(TYPE_OPTIONS).toEqual([
			'CONNECT',
			'RECOVER',
			'STOP',
			'HOME',
			'SORT',
			'FEED',
			'CONFIGURE'
		]);
	});

	it('labels every option it offers', () => {
		for (const state of STATE_OPTIONS) {
			expect(stateLabel(state)).not.toBe('');
		}
		for (const type of TYPE_OPTIONS) {
			expect(typeLabel(type)).not.toBe('');
		}
	});
});

describe('a page of history', () => {
	it('carries the actor and the created time beside the same reading the dashboard would show', () => {
		const plan = historyPlan(page([operation()]), { state: null, type: null });

		expect(plan.rows).toHaveLength(1);
		expect(plan.rows[0]).toMatchObject({
			operationId: '0b5f2a7c-1d3e-4f5a-8b9c-0d1e2f3a4b5c',
			title: 'Sort',
			createdAt: '2026-08-11T12:00:00.000Z',
			actorUserId: 'u-1',
			actorRole: 'operator'
		});
		expect(plan.empty).toBe(false);
	});

	it('never words an unsettled row as a completion', () => {
		const plan = historyPlan(page([operation({ state: 'ACCEPTED' })]), {
			state: null,
			type: null
		});

		expect(plan.rows[0].summary).not.toMatch(COMPLETION_WORDS);
	});

	it('describes an unconfirmed terminal as not known, not by its state name alone', () => {
		const plan = historyPlan(
			page([
				operation({
					state: 'UNCERTAIN',
					trusted_terminal: false,
					terminal_at: '2026-08-11T12:00:05.000Z',
					outcome: 'UNCERTAIN'
				})
			]),
			{ state: null, type: null }
		);

		expect(plan.rows[0].summary).toContain('not known');
	});

	it('carries the daemon cursor forward untouched', () => {
		expect(historyPlan(page([], 'opaque-cursor-1'), { state: null, type: null }).nextCursor).toBe(
			'opaque-cursor-1'
		);
		expect(historyPlan(page([]), { state: null, type: null }).nextCursor).toBeNull();
	});

	it('tells an empty unfiltered page apart from an empty filtered one', () => {
		expect(historyPlan(page([]), { state: null, type: null }).emptyMessage).toBe(
			'No operations recorded yet.'
		);
		expect(historyPlan(page([]), { state: 'SUCCEEDED', type: null }).emptyMessage).toBe(
			'No operations match this filter.'
		);
		expect(historyPlan(page([]), { state: null, type: 'SORT' }).emptyMessage).toBe(
			'No operations match this filter.'
		);
	});
});

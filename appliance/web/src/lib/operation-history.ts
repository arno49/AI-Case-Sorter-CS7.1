/**
 * What the history screen may say about the operations it lists.
 *
 * A list carries the same commitments as the single reading on the dashboard,
 * repeated per row: acceptance is still not completion, and an unconfirmed
 * terminal is still described as not known rather than by repeating its state
 * name. `operationReading` from `machine-status.ts` is reused rather than
 * reimplemented, so the two screens cannot describe the same operation two
 * different ways.
 *
 * The filters this screen offers are exactly the vocabulary the daemon
 * accepts for `state` and `type` — nothing here invents a grouping or a label
 * the contract does not already have a value for.
 */

import { OPERATION_TITLES, operationReading, type OperationReading } from '$lib/machine-status';
import type { Operation, OperationPage, OperationState, OperationType } from '$lib/machine';

export const STATE_OPTIONS: readonly OperationState[] = [
	'QUEUED',
	'ACCEPTED',
	'RUNNING',
	'SUCCEEDED',
	'FAILED',
	'CANCELLED',
	'UNCERTAIN'
];

export const TYPE_OPTIONS: readonly OperationType[] = [
	'CONNECT',
	'RECOVER',
	'STOP',
	'HOME',
	'SORT',
	'FEED',
	'CONFIGURE'
];

const STATE_LABELS: Readonly<Record<OperationState, string>> = Object.freeze({
	QUEUED: 'Queued',
	ACCEPTED: 'Accepted',
	RUNNING: 'Running',
	SUCCEEDED: 'Succeeded',
	FAILED: 'Failed',
	CANCELLED: 'Cancelled',
	UNCERTAIN: 'Uncertain'
});

/** Naming a filter value is not describing an outcome, so this is safe to say
 *  of any state, including one this module elsewhere refuses to call settled. */
export function stateLabel(state: OperationState): string {
	return STATE_LABELS[state];
}

export function typeLabel(type: OperationType): string {
	return OPERATION_TITLES[type];
}

export interface HistoryRow extends OperationReading {
	readonly createdAt: string;
	readonly actorUserId: string;
	readonly actorRole: string;
}

function historyRow(operation: Operation): HistoryRow {
	return {
		...operationReading(operation),
		createdAt: operation.created_at,
		actorUserId: operation.actor.user_id,
		actorRole: operation.actor.role
	};
}

export interface HistoryFilter {
	readonly state: OperationState | null;
	readonly type: OperationType | null;
}

export interface HistoryPlan {
	readonly rows: readonly HistoryRow[];
	/** The daemon's own cursor for the next page, or `null` at the end. */
	readonly nextCursor: string | null;
	readonly empty: boolean;
	readonly emptyMessage: string;
}

const NO_OPERATIONS = 'No operations recorded yet.';
const NO_MATCHING_OPERATIONS = 'No operations match this filter.';

export function historyPlan(page: OperationPage, filter: HistoryFilter): HistoryPlan {
	const rows = page.items.map(historyRow);
	const filtered = filter.state !== null || filter.type !== null;
	return {
		rows,
		nextCursor: page.next_cursor ?? null,
		empty: rows.length === 0,
		emptyMessage: filtered ? NO_MATCHING_OPERATIONS : NO_OPERATIONS
	};
}

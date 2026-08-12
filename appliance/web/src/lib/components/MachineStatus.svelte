<script lang="ts">
	import {
		activeOperationReading,
		connectionReading,
		faultReadings,
		faultSummary,
		homingReadings,
		readinessReading
	} from '$lib/machine-status';
	import type { MachineSnapshot } from '$lib/machine';

	interface Props {
		/** The machine as last read, or `null` when none has been read yet. */
		readonly snapshot: MachineSnapshot | null;
		/** Why the machine cannot be reached, in the words the server chose. */
		readonly unavailable: string | null;
		/** A snapshot is owed and has not been read: what is shown may be behind. */
		readonly stale?: boolean;
		/** A read is in flight. */
		readonly refreshing?: boolean;
	}

	let { snapshot, unavailable, stale = false, refreshing = false }: Props = $props();

	const connection = $derived(snapshot && connectionReading(snapshot));
	const readiness = $derived(snapshot && readinessReading(snapshot));
	const homing = $derived(snapshot ? homingReadings(snapshot) : []);
	const active = $derived(snapshot && activeOperationReading(snapshot));
	const faults = $derived(snapshot ? faultReadings(snapshot) : []);
	const summary = $derived(snapshot && faultSummary(snapshot));
</script>

<section aria-labelledby="machine-status">
	<h2 id="machine-status">Machine</h2>

	{#if stale}
		<!-- The page owes itself a snapshot. Saying so is the point: a screen that
		     is behind must not look like one that is current. -->
		<p role="status" data-tone="uncertain">
			This view may be out of date. Reading the machine{refreshing ? '…' : ''}
		</p>
	{/if}

	{#if unavailable}
		<p role="alert" data-tone="attention">{unavailable}</p>
	{:else if snapshot && connection && readiness && summary}
		<dl>
			<dt>Connection</dt>
			<dd data-field="connection" data-tone={connection.tone}>
				{connection.label}
				{#if connection.detail}
					<span>{connection.detail}</span>
				{/if}
			</dd>

			<dt>Work</dt>
			<dd data-field="readiness" data-tone={readiness.tone}>
				{readiness.label}
				{#if readiness.detail}
					<span>{readiness.detail}</span>
				{/if}
			</dd>

			{#each homing as axis (axis.axis)}
				<dt>{axis.axis} homing</dt>
				<dd data-field="homing" data-axis={axis.axis} data-tone={axis.tone}>{axis.label}</dd>
			{/each}

			<dt>Active operation</dt>
			<dd data-field="active-operation" data-tone={active ? active.tone : 'ordinary'}>
				{#if active}
					<!-- The state name is never left to stand on its own: what it means for
					     a machine that can move is the sentence beside it. -->
					{active.title} — {active.state}
					<span data-field="active-operation-summary">{active.summary}</span>
					<span data-field="active-operation-id">Operation {active.operationId}</span>
				{:else}
					None
				{/if}
			</dd>

			<dt>Faults</dt>
			<dd data-field="fault-state" data-tone={summary.tone}>
				{summary.label}
				{#if summary.detail}
					<span>{summary.detail}</span>
				{/if}
			</dd>

			<dt>Snapshot generation</dt>
			<dd data-field="generation">{snapshot.generation}</dd>

			<dt>Observed at</dt>
			<dd data-field="observed-at">{snapshot.observed_at}</dd>
		</dl>

		{#if faults.length > 0}
			<ul aria-label="Recorded faults">
				{#each faults as fault (fault.faultId)}
					<li data-field="fault" data-tone={fault.tone}>
						{fault.code} ({fault.source}) — {fault.state}, opened {fault.openedAt}
						{#if fault.message}
							<span>{fault.message}</span>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}
	{:else}
		<p role="status">The machine has not been read yet.</p>
	{/if}
</section>

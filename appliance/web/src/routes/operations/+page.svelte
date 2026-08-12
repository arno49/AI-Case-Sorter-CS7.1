<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		historyPlan,
		stateLabel,
		STATE_OPTIONS,
		typeLabel,
		TYPE_OPTIONS
	} from '$lib/operation-history';

	let { data } = $props();

	const plan = $derived(data.page ? historyPlan(data.page, data.filter) : null);
</script>

<svelte:head>
	<title>Operation history — CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>Operation history</h1>

	<p><a href={resolve('/')}>Back to the dashboard</a></p>

	<form method="GET">
		<label>
			State
			<select name="state">
				<option value="" selected={data.filter.state === null}>All</option>
				{#each STATE_OPTIONS as state (state)}
					<option value={state} selected={data.filter.state === state}>{stateLabel(state)}</option>
				{/each}
			</select>
		</label>
		<label>
			Type
			<select name="type">
				<option value="" selected={data.filter.type === null}>All</option>
				{#each TYPE_OPTIONS as type (type)}
					<option value={type} selected={data.filter.type === type}>{typeLabel(type)}</option>
				{/each}
			</select>
		</label>
		<button type="submit">Filter</button>
	</form>

	{#if data.unavailable}
		<p role="alert" data-tone="attention">{data.unavailable}</p>
	{:else if plan}
		{#if plan.empty}
			<p role="status" data-field="history-empty">{plan.emptyMessage}</p>
		{:else}
			<ul aria-label="Operations">
				{#each plan.rows as row (row.operationId)}
					<li data-field="history-row" data-tone={row.tone}>
						{row.title} — {row.state}
						<span data-field="history-summary">{row.summary}</span>
						<span data-field="history-operation-id">Operation {row.operationId}</span>
						<span data-field="history-created-at">Created {row.createdAt}</span>
						<span data-field="history-actor">{row.actorUserId} ({row.actorRole})</span>
					</li>
				{/each}
			</ul>

			{#if plan.nextCursor !== null}
				{@const query = new URLSearchParams({
					...(data.filter.state !== null ? { state: data.filter.state } : {}),
					...(data.filter.type !== null ? { type: data.filter.type } : {}),
					cursor: plan.nextCursor
				}).toString()}
				<a data-field="history-next" href={resolve(`/operations?${query}`)}> Next page </a>
			{/if}
		{/if}
	{/if}
</main>

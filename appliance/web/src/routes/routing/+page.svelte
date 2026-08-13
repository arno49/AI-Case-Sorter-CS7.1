<script lang="ts">
	import { resolve } from '$app/paths';

	let { data, form } = $props();

	function classLabel(classId: number | null, overflow: boolean): string {
		if (overflow) return 'everything else';
		return `class ${classId}`;
	}
</script>

<svelte:head>
	<title>Routing — CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>Routing</h1>

	<p><a href={resolve('/')}>Back to the dashboard</a></p>

	{#if data.unavailable}
		<p role="alert" data-tone="attention">{data.unavailable}</p>
	{:else if data.routing}
		{#if data.routing.active}
			<section aria-labelledby="routing-active">
				<h2 id="routing-active">Active run</h2>
				<!-- The active profile is visible throughout the run (ADR-0013) -
				     this is the one place that is always true, not just at the
				     moment a run started. -->
				<p data-field="routing-kind">
					Profile: {data.routing.kind}
					{#if data.routing.sourceGroup !== null}
						<span>(refining chute {data.routing.sourceGroup})</span>
					{/if}
				</p>
				<p data-field="routing-started-at">Started {data.routing.startedAt}</p>

				{#if data.routing.legend.length > 0}
					<table>
						<thead>
							<tr>
								<th scope="col">Chute</th>
								<th scope="col">Routes</th>
							</tr>
						</thead>
						<tbody>
							{#each data.routing.legend as entry (entry.slot)}
								<tr data-field="routing-legend-{entry.slot}">
									<td>{entry.slot}</td>
									<td>{classLabel(entry.classId, entry.overflow)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{:else}
					<p data-field="routing-legend-empty">No chute has been claimed yet.</p>
				{/if}

				{#if data.canOperate}
					<form method="POST" action="?/stop">
						<input type="hidden" name="csrf_token" value={data.csrfToken} />
						<button type="submit">Stop this run</button>
					</form>
				{/if}
				{#if form?.control === 'stop'}
					{#if form.error}
						<p role="alert" data-field="routing-stop-error">{form.error}</p>
					{:else}
						<p role="status" data-field="routing-stop-status">The run has stopped.</p>
					{/if}
				{/if}
			</section>
		{:else if data.canOperate}
			<section aria-labelledby="routing-start">
				<h2 id="routing-start">Start a run</h2>

				<article>
					<h3>Fixed map</h3>
					<p>
						Pre-assign specific classes to specific chutes, with one overflow chute for everything
						else.
					</p>
					<form method="POST" action="?/startFixed">
						<input type="hidden" name="csrf_token" value={data.csrfToken} />
						<label>
							Class:chute pairs (one per line, e.g. <code>12:3</code>)
							<textarea name="class_to_slot" data-field="fixed-class-to-slot"></textarea>
						</label>
						<label>
							Overflow chute
							<input
								type="text"
								inputmode="numeric"
								name="overflow_slot"
								data-field="fixed-overflow-slot"
							/>
						</label>
						<button type="submit">Start fixed-map run</button>
					</form>
				</article>

				<article>
					<h3>Dynamic per-batch</h3>
					<p>
						The first classes seen in this run claim a chute, in the order they first appear, until
						every listed chute is claimed.
					</p>
					<form method="POST" action="?/startDynamic">
						<input type="hidden" name="csrf_token" value={data.csrfToken} />
						<label>
							Available chutes (one per line)
							<textarea name="available_slots" data-field="dynamic-available-slots"></textarea>
						</label>
						<button type="submit">Start dynamic run</button>
					</form>
				</article>

				<article>
					<h3>Two-pass, second pass</h3>
					<p>
						The same shape as a fixed map, refining exactly one prior pass's output chute more
						finely - feed only that chute's cases into this run.
					</p>
					<form method="POST" action="?/startTwoPass">
						<input type="hidden" name="csrf_token" value={data.csrfToken} />
						<label>
							Class:chute pairs (one per line)
							<textarea name="class_to_slot" data-field="two-pass-class-to-slot"></textarea>
						</label>
						<label>
							Overflow chute
							<input
								type="text"
								inputmode="numeric"
								name="overflow_slot"
								data-field="two-pass-overflow-slot"
							/>
						</label>
						<label>
							Prior pass's chute being refined (leave blank for a first pass)
							<input
								type="text"
								inputmode="numeric"
								name="source_group"
								data-field="two-pass-source-group"
							/>
						</label>
						<button type="submit">Start two-pass run</button>
					</form>
				</article>

				{#if form?.control === 'start'}
					{#if form.error}
						<p role="alert" data-field="routing-start-error">{form.error}</p>
					{:else if form.kind}
						<p role="status" data-field="routing-start-status">Started a {form.kind} run.</p>
					{/if}
				{/if}
			</section>
		{:else}
			<p data-field="routing-inactive">No routing run is active.</p>
		{/if}
	{/if}
</main>

<script lang="ts">
	import { resolve } from '$app/paths';
	import { classReadings, trainingReadinessDetail } from '$lib/dataset-view';

	let { data } = $props();

	const readings = $derived(data.dataset ? classReadings(data.dataset) : []);
	const readiness = $derived(data.dataset ? trainingReadinessDetail(data.dataset) : null);
</script>

<svelte:head>
	<title>Dataset — CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>Dataset</h1>

	<p><a href={resolve('/')}>Back to the dashboard</a></p>

	{#if data.unavailable}
		<p role="alert" data-tone="attention">{data.unavailable}</p>
	{:else if data.dataset}
		<p
			data-field="dataset-readiness"
			data-tone={data.dataset.trainingReady ? 'ordinary' : 'attention'}
		>
			{readiness}
		</p>
		{#if readings.length > 0}
			<dl>
				{#each readings as reading (reading.slot)}
					<dt>Slot {reading.slot}</dt>
					<dd data-field="dataset-class-{reading.slot}" data-tone={reading.tone}>
						{reading.label}
						<span>(floor {reading.minimum})</span>
						{#if !reading.eligible}
							<span data-field="dataset-class-{reading.slot}-reason"
								>Ineligible: {reading.detail}</span
							>
						{:else}
							<span>{reading.detail}</span>
						{/if}
					</dd>
				{/each}
			</dl>
		{/if}
	{/if}
</main>

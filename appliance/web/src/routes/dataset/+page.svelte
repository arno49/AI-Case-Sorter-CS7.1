<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		classReadings,
		modelReadings,
		suggestionAccuracyDetail,
		trainingReadinessDetail
	} from '$lib/dataset-view';

	let { data, form } = $props();

	const readings = $derived(data.dataset ? classReadings(data.dataset) : []);
	const readiness = $derived(data.dataset ? trainingReadinessDetail(data.dataset) : null);
	const models = $derived(data.models ? modelReadings(data.models) : []);
	const suggestionAccuracy = $derived(
		data.suggestionAccuracy ? suggestionAccuracyDetail(data.suggestionAccuracy) : null
	);

	function percent(accuracy: number | null): string {
		return accuracy === null ? 'not evaluated' : `${Math.round(accuracy * 100)}%`;
	}
</script>

<svelte:head>
	<title>Dataset — CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>Dataset</h1>

	<p><a href={resolve('/')}>Back to the dashboard</a></p>

	{#if data.unavailable}
		<p role="alert" data-tone="attention">{data.unavailable}</p>
	{:else}
		{#if data.dataset}
			<section aria-labelledby="dataset-classes">
				<h2 id="dataset-classes">Examples per class</h2>
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

				{#if data.canTrain}
					<form method="POST" action="?/train">
						<input type="hidden" name="csrf_token" value={data.csrfToken} />
						<button type="submit" disabled={!data.dataset.trainingReady}>
							Train a new candidate
						</button>
					</form>
					{#if form?.control === 'train'}
						{#if form.error}
							<p role="alert" data-field="train-error">{form.error}</p>
						{:else if form.started === false}
							<p role="status" data-field="train-status">A training run was already in progress.</p>
						{:else if form.started === true}
							<p role="status" data-field="train-status">Training started in the background.</p>
						{/if}
					{/if}
				{/if}
			</section>
		{/if}

		{#if data.models}
			<section aria-labelledby="dataset-models">
				<h2 id="dataset-models">Model versions</h2>

				{#if models.length === 0}
					<p data-field="models-empty">No candidate has been trained yet.</p>
				{:else}
					{#each models as model (model.version)}
						<article data-field="model-{model.version}">
							<h3>
								Version {model.version}
								{#if model.active}
									<span data-field="model-{model.version}-active">(active)</span>
								{/if}
							</h3>
							<p>Trained {model.trainedAt}</p>
							<p>Included classes: {model.includedClasses.join(', ') || 'none'}</p>
							{#if model.excludedClasses.length > 0}
								<p>Excluded (below the floor): {model.excludedClasses.join(', ')}</p>
							{/if}
							{#if model.accuracyComparison.length > 0}
								<!-- ADR-0013: activation is refused unless the operator has been
								     shown the candidate's accuracy alongside the currently active
								     model's - this table is that comparison, rendered before the
								     activate control below it, not a separate confirmation step. -->
								<table>
									<thead>
										<tr>
											<th scope="col">Slot</th>
											<th scope="col">This candidate</th>
											<th scope="col">Currently active</th>
										</tr>
									</thead>
									<tbody>
										{#each model.accuracyComparison as row (row.slot)}
											<tr>
												<td>{row.slot}</td>
												<td>{percent(row.candidateAccuracy)}</td>
												<td>{percent(row.activeAccuracy)}</td>
											</tr>
										{/each}
									</tbody>
								</table>
							{/if}
							{#if data.canTrain && !model.active}
								<form method="POST" action="?/activate">
									<input type="hidden" name="csrf_token" value={data.csrfToken} />
									<input type="hidden" name="version" value={model.version} />
									<button type="submit">Activate this version</button>
								</form>
							{/if}
						</article>
					{/each}
				{/if}

				{#if form?.control === 'activate'}
					{#if form.error}
						<p role="alert" data-field="activate-error">{form.error}</p>
					{:else if form.activeVersion}
						<p role="status" data-field="activate-status">
							Version {form.activeVersion} is now active.
						</p>
					{/if}
				{/if}

				{#if data.canTrain && data.models.canRollBack}
					<form method="POST" action="?/rollback">
						<input type="hidden" name="csrf_token" value={data.csrfToken} />
						<button type="submit">Roll back to the previous version</button>
					</form>
				{/if}
				{#if form?.control === 'rollback'}
					{#if form.error}
						<p role="alert" data-field="rollback-error">{form.error}</p>
					{:else if form.activeVersion}
						<p role="status" data-field="rollback-status">
							Rolled back to version {form.activeVersion}.
						</p>
					{/if}
				{/if}
			</section>
		{/if}

		{#if data.suggestionAccuracy}
			<section aria-labelledby="suggestion-accuracy">
				<h2 id="suggestion-accuracy">Live suggestion accuracy</h2>
				<!-- Separate evidence from the training-time held-out accuracy shown
				     above: this is measured against what the operator actually chose
				     after a suggestion was shown, not a held-out split of the training
				     data (PI-VISION-006). -->
				<p data-field="suggestion-accuracy">{suggestionAccuracy}</p>
			</section>
		{/if}
	{/if}
</main>

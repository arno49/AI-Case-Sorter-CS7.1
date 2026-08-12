<script lang="ts">
	import { acceptedWording, type ControlsPlan } from '$lib/machine-controls';

	/** Which form the last answer belongs to, so it renders beside that form. */
	interface Feedback {
		readonly control: string;
		readonly error?: string;
		readonly operationId?: string;
	}

	/**
	 * One key per form, minted by the server load for this render. A resubmission
	 * of the same rendered form is therefore the same command to the daemon, and
	 * the re-render after an answer mints fresh keys for the next intent.
	 */
	interface CommandKeys {
		readonly connect: string;
		readonly home: string;
		readonly sort: string;
		readonly feed: string;
	}

	let {
		plan,
		csrfToken,
		keys = null,
		feedback = null
	}: {
		plan: ControlsPlan;
		csrfToken: string;
		keys?: CommandKeys | null;
		feedback?: Feedback | null;
	} = $props();
</script>

{#snippet intent(key: string)}
	<!-- Every command names the machine state it was decided against and the
	     identity that makes a resubmission the same command. -->
	<input type="hidden" name="csrf_token" value={csrfToken} />
	<input type="hidden" name="generation" value={plan.generation} />
	<input type="hidden" name="idempotency_key" value={key} />
{/snippet}

{#snippet reason(decision: { reason: string | null }, control: string)}
	{#if decision.reason !== null}
		<p data-field="control-reason" data-control={control}>{decision.reason}</p>
	{/if}
{/snippet}

{#snippet outcome(control: string)}
	{#if feedback?.control === control}
		{#if feedback.error}
			<p role="alert" data-field="command-error">{feedback.error}</p>
		{:else if feedback.operationId}
			<p role="status" data-field="command-accepted">{acceptedWording(feedback.operationId)}</p>
		{/if}
	{/if}
{/snippet}

{#if plan.offered && keys !== null}
	<section aria-labelledby="manual-controls">
		<h2 id="manual-controls">Manual controls</h2>

		{#if plan.withheld !== null || plan.generation === null}
			<p data-field="controls-withheld">{plan.withheld}</p>
		{:else}
			<form method="POST" action="?/connect">
				{@render intent(keys.connect)}
				<button type="submit" disabled={!plan.connect.enabled}>Connect to the controller</button>
				{@render reason(plan.connect, 'connect')}
				{@render outcome('connect')}
			</form>

			<form method="POST" action="?/home">
				{@render intent(keys.home)}
				{#if plan.home.enabled}
					<label>
						Axis to home
						<select name="target">
							<option value="feeder">Feeder</option>
							<option value="sorter">Sorter</option>
							<option value="both">Both</option>
						</select>
					</label>
				{/if}
				<button type="submit" disabled={!plan.home.enabled}>Home</button>
				{@render reason(plan.home, 'home')}
				{@render outcome('home')}
			</form>

			<form method="POST" action="?/sort">
				{@render intent(keys.sort)}
				{#if plan.sort.enabled}
					<label>
						Slot
						<select name="slot" data-field="sort-slots">
							{#each plan.sort.slots as slot (slot)}
								<option value={slot}>{slot}</option>
							{/each}
						</select>
					</label>
				{/if}
				<button type="submit" disabled={!plan.sort.enabled}>Move the sorter</button>
				{@render reason(plan.sort, 'sort')}
				{@render outcome('sort')}
			</form>

			<form method="POST" action="?/feed">
				{@render intent(keys.feed)}
				<button type="submit" disabled={!plan.feed.enabled}>Feed one case</button>
				{@render reason(plan.feed, 'feed')}
				{@render outcome('feed')}
			</form>
		{/if}
	</section>
{/if}

<script lang="ts">
	import { acceptedWording, type RecoveryPlan } from '$lib/machine-controls';

	interface Feedback {
		readonly control: string;
		readonly error?: string;
		readonly operationId?: string;
	}

	let {
		plan,
		csrfToken,
		recoveryKey = null,
		feedback = null
	}: {
		plan: RecoveryPlan;
		csrfToken: string;
		recoveryKey?: string | null;
		feedback?: Feedback | null;
	} = $props();
</script>

{#if plan.offered && recoveryKey !== null}
	<section aria-labelledby="recovery">
		<h2 id="recovery">Recovery</h2>

		{#if plan.withheld !== null || plan.generation === null}
			<p data-field="recovery-withheld">{plan.withheld}</p>
		{:else}
			<form method="POST" action="?/recover">
				<!-- Every command names the machine state it was decided against and
				     the identity that makes a resubmission the same command. -->
				<input type="hidden" name="csrf_token" value={csrfToken} />
				<input type="hidden" name="generation" value={plan.generation} />
				<input type="hidden" name="idempotency_key" value={recoveryKey} />

				<label>
					<input type="checkbox" name="confirm" value="true" required />
					I understand this tears down the current session and starts again from a fresh transport.
				</label>

				<button type="submit" disabled={!plan.decision.enabled}>Attempt recovery</button>

				{#if plan.decision.reason !== null}
					<p data-field="recovery-reason">{plan.decision.reason}</p>
				{/if}

				{#if feedback?.control === 'recover'}
					{#if feedback.error}
						<p role="alert" data-field="recovery-error">{feedback.error}</p>
					{:else if feedback.operationId}
						<p role="status" data-field="recovery-accepted">
							{acceptedWording(feedback.operationId)}
						</p>
					{/if}
				{/if}
			</form>
		{/if}
	</section>
{/if}

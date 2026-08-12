<script lang="ts">
	interface Props {
		/** The token this appliance issued to this browser. */
		readonly csrfToken: string;
		/** What the server said when the last stop was refused. */
		readonly error?: string | null;
		/** The identity the daemon gave the last stop it accepted. */
		readonly accepted?: { readonly operationId: string; readonly state: string } | null;
	}

	let { csrfToken, error = null, accepted = null }: Props = $props();
</script>

<!--
	The stop control, first on the page.

	Its position is the point. This is the control an operator reaches for when
	something is wrong, so it is first in the document and therefore first in the
	tab order — nothing has to be read, scrolled past or dismissed to get to it,
	and no element on this page takes a `tabindex` that would put itself in front.

	It is a software stop. It is not an emergency stop, and the page says so
	beside the button rather than somewhere an operator has to go looking.
-->
<section aria-labelledby="stop-the-machine">
	<h2 id="stop-the-machine">Stop</h2>

	<form method="POST" action="?/stop">
		<input type="hidden" name="csrf_token" value={csrfToken} />
		<button type="submit" id="stop-the-machine-control">Stop the machine</button>
	</form>

	<p>
		This is a software stop. It is not an emergency stop and is no substitute for the physical
		emergency stop and the guarded motor-power path.
	</p>

	{#if error}
		<p role="alert" data-field="stop-error">{error}</p>
	{/if}
	{#if accepted}
		<!-- Accepted is not finished: the daemon has taken the command and given it
		     an identity, and the machine may still be moving. -->
		<p role="status" data-field="stop-accepted">
			Stop accepted as operation {accepted.operationId} ({accepted.state}). This is an acceptance,
			not a result — watch the machine.
		</p>
	{/if}
</section>

<script lang="ts">
	let { data, form } = $props();

	// Labels for what the server said this role may do. The list is a reflection
	// of the server's decision, not a source of it: hiding an entry here hides a
	// control, it does not withhold permission.
	const LABELS: Record<string, string> = {
		'machine.read': 'View machine state, history and faults',
		'machine.stop': 'Stop the machine (software stop, not an emergency stop)',
		'machine.operate': 'Connect, home, sort and feed',
		'machine.recover': 'Recovery and reset, with explicit confirmation',
		'config.write': 'Change permitted configuration',
		'users.manage': 'Manage users and provisioning'
	};
</script>

<svelte:head>
	<title>CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>CS7.1 control appliance</h1>

	<p>Signed in as {data.username} ({data.role}).</p>

	<section aria-labelledby="machine">
		<h2 id="machine">Machine</h2>

		{#if data.unavailable}
			<p role="alert">{data.unavailable}</p>
		{:else if data.snapshot}
			<dl>
				<dt>Connection</dt>
				<dd>{data.snapshot.connection_state}</dd>
				<dt>Faults</dt>
				<dd>{data.snapshot.fault_state} ({data.snapshot.faults?.length ?? 0} recorded)</dd>
				<dt>Ready</dt>
				<dd>
					{data.snapshot.ready ? 'yes' : 'no'}
					{#if data.snapshot.readiness_reason}
						— {data.snapshot.readiness_reason}
					{/if}
				</dd>
				<dt>Snapshot generation</dt>
				<dd>{data.snapshot.generation}</dd>
				<dt>Active operation</dt>
				<dd>
					{#if data.snapshot.active_operation}
						{data.snapshot.active_operation.type} — {data.snapshot.active_operation.state}
					{:else}
						none
					{/if}
				</dd>
			</dl>
			<p>
				Readiness is the daemon's own session readiness. It is not a statement about physical
				clearance, homing or energy isolation.
			</p>
		{/if}
	</section>

	<section aria-labelledby="stop">
		<h2 id="stop">Stop</h2>

		{#if form?.error}
			<p role="alert">{form.error}</p>
		{/if}
		{#if form?.operationId}
			<!-- Accepted is not finished: the daemon has taken the command and given
			     it an identity, and the machine may still be moving. -->
			<p role="status">
				Stop accepted as operation {form.operationId} ({form.state}). This is an acceptance, not a
				completion — watch the machine.
			</p>
		{/if}

		<form method="POST" action="?/stop">
			<input type="hidden" name="csrf_token" value={data.csrfToken} />
			<button type="submit">Stop the machine</button>
		</form>
		<p>
			This is a software stop. It is not an emergency stop and is no substitute for the physical
			emergency stop and the guarded motor-power path.
		</p>
	</section>

	<section aria-labelledby="permitted">
		<h2 id="permitted">Permitted for this account</h2>
		<ul>
			{#each data.capabilities as capability (capability)}
				<li>{LABELS[capability] ?? capability}</li>
			{/each}
		</ul>
		<p>Connect, home, sort and feed controls are not implemented yet.</p>
	</section>

	<form method="POST" action="/logout">
		<!-- The server refuses this post without the token it issued. -->
		<input type="hidden" name="csrf_token" value={data.csrfToken} />
		<button type="submit">Sign out</button>
	</form>
</main>

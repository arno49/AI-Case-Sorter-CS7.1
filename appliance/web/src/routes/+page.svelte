<script lang="ts">
	let { data } = $props();

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

	<h2>Permitted for this account</h2>
	<ul>
		{#each data.capabilities as capability (capability)}
			<li>{LABELS[capability] ?? capability}</li>
		{/each}
	</ul>

	<p>Machine controls are not implemented yet.</p>

	<form method="POST" action="/logout">
		<!-- The server refuses this post without the token it issued. -->
		<input type="hidden" name="csrf_token" value={data.csrfToken} />
		<button type="submit">Sign out</button>
	</form>
</main>

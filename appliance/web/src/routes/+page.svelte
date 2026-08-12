<script lang="ts">
	let { data } = $props();
</script>

<svelte:head>
	<title>CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>CS7.1 control appliance</h1>

	<p>Signed in as {data.username} ({data.role}).</p>
	<p>Machine controls are not implemented yet.</p>

	<form method="POST" action="/logout">
		<button type="submit">Sign out</button>
	</form>
</main>

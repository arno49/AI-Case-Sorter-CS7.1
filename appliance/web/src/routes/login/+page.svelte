<script lang="ts">
	let { data, form } = $props();
</script>

<svelte:head>
	<title>Sign in — CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>CS7.1 control appliance</h1>

	{#if data.notice}
		<p role="status">{data.notice}</p>
	{/if}
	{#if form?.error}
		<p role="alert">{form.error}</p>
	{/if}

	<form method="POST">
		<label for="username">Username</label>
		<input id="username" name="username" autocomplete="username" required />

		<label for="password">Password</label>
		<input id="password" name="password" type="password" autocomplete="current-password" required />

		<button type="submit">Sign in</button>
	</form>
</main>

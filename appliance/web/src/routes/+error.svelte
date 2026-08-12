<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
</script>

<svelte:head>
	<title>{page.status} — CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>{page.status}</h1>
	<!-- The server's message only ever states that the account is not permitted;
	     it never names the capability or the route that was refused. -->
	<p>{page.error?.message ?? 'Something went wrong.'}</p>
	<p><a href={resolve('/')}>Return to the dashboard</a></p>
</main>

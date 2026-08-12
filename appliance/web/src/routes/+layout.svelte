<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

{@render children()}

<!--
	`data-tone` is decided in `$lib/machine-status.ts`, never here — this only
	renders the tone that module already chose. `uncertain` is styled more
	loudly than `attention`, not as a shade of it: a machine whose state is not
	known is a different situation from one with a known problem, and an
	operator scanning the page must not be able to mistake one for the other.
-->
<style>
	:global([data-tone='uncertain']) {
		display: inline-block;
		border: 2px solid #6a1b9a;
		border-radius: 0.25em;
		padding: 0.1em 0.4em;
		background: #f3e5f5;
		color: #4a148c;
		font-weight: 700;
	}

	:global([data-tone='attention']) {
		color: #8a4b00;
		font-weight: 600;
	}
</style>

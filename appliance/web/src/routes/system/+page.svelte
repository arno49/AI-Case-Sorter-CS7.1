<script lang="ts">
	import { resolve } from '$app/paths';
	import {
		dtrGateReading,
		firmwareVersionReading,
		journalReading,
		NOT_REPORTED_STORAGE
	} from '$lib/system-view';

	let { data } = $props();

	const firmware = $derived(data.snapshot ? firmwareVersionReading(data.snapshot) : null);
	const journal = $derived(data.snapshot ? journalReading(data.snapshot) : null);
	const dtrGate = $derived(data.system ? dtrGateReading(data.system) : null);
</script>

<svelte:head>
	<title>System — CS7.1 control appliance</title>
</svelte:head>

<main>
	<h1>System</h1>

	<p><a href={resolve('/')}>Back to the dashboard</a></p>

	{#if data.unavailable}
		<p role="alert" data-tone="attention">{data.unavailable}</p>
	{:else if firmware && journal && dtrGate}
		<dl>
			<dt>Firmware version</dt>
			<dd data-field="system-firmware" data-tone={firmware.tone}>
				{firmware.label}
				<span>{firmware.detail}</span>
			</dd>

			<dt>Journal</dt>
			<dd data-field="system-journal" data-tone={journal.tone}>
				{journal.label}
				<span>{journal.detail}</span>
			</dd>

			<dt>Storage</dt>
			<dd data-field="system-storage">{NOT_REPORTED_STORAGE}</dd>

			<dt>DTR gate (Linux/POSIX real serial ports)</dt>
			<dd data-field="system-dtr-gate" data-tone={dtrGate.tone}>
				{dtrGate.label}
				<span>{dtrGate.detail}</span>
			</dd>
		</dl>
	{/if}
</main>

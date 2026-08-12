<script lang="ts">
	import { onMount } from 'svelte';
	import { invalidateAll } from '$app/navigation';
	import { resolve } from '$app/paths';

	import MachineStatus from '$lib/components/MachineStatus.svelte';
	import ManualControls from '$lib/components/ManualControls.svelte';
	import RecoveryControl from '$lib/components/RecoveryControl.svelte';
	import StopControl from '$lib/components/StopControl.svelte';
	import { controlsPlan, recoveryPlan } from '$lib/machine-controls';
	import { MachineView } from '$lib/machine-view.svelte';
	import type { MachineSnapshot } from '$lib/machine';

	let { data, form } = $props();

	/**
	 * The machine as this page believes it to be.
	 *
	 * Reading a snapshot is re-running this page's server load, which already
	 * authorizes the request and translates the daemon's answer. Adding a second
	 * way for the browser to read the machine would be a second thing to keep
	 * honest.
	 */
	const view = new MachineView<MachineSnapshot, { generation: number }>(async () => {
		await invalidateAll();
		return data.snapshot ?? null;
	});

	// Until a read of its own has succeeded, what the server rendered is what
	// there is to show; after one, the view is the newer of the two.
	const machine = $derived(view.snapshot ?? data.snapshot);
	const unavailable = $derived(view.unavailable ?? data.unavailable);

	onMount(() => {
		// The browser's own reconnection is the retry policy: it re-sends the last
		// cursor it saw, and the server decides whether that can be resumed.
		const stream = new EventSource('/events');
		stream.addEventListener('resync', () => void view.resynchronise());
		stream.addEventListener('unavailable', (frame) =>
			view.disconnected(readMessage(frame as MessageEvent))
		);
		stream.addEventListener('message', (frame) => received(frame as MessageEvent));
		for (const type of ['snapshot.changed', 'operation.changed', 'fault.changed']) {
			stream.addEventListener(type, (frame) => received(frame as MessageEvent));
		}
		return () => stream.close();
	});

	function received(frame: MessageEvent): void {
		const generation = parseGeneration(frame.data);
		if (generation !== null) {
			view.received({ generation });
		}
	}

	function parseGeneration(data: string): number | null {
		try {
			const parsed: unknown = JSON.parse(data);
			const generation = (parsed as { generation?: unknown }).generation;
			return typeof generation === 'number' ? generation : null;
		} catch {
			return null;
		}
	}

	function readMessage(frame: MessageEvent): string {
		try {
			const parsed: unknown = JSON.parse(frame.data as string);
			const message = (parsed as { message?: unknown }).message;
			return typeof message === 'string' ? message : 'The machine service is not answering.';
		} catch {
			return 'The machine service is not answering.';
		}
	}

	// Every action names the control it answered, so an answer renders beside
	// the form the operator used and nowhere else.
	const stopFeedback = $derived(form?.control === 'stop' ? form : null);
	const accepted = $derived(
		stopFeedback?.operationId
			? { operationId: stopFeedback.operationId, state: String(stopFeedback.state) }
			: null
	);
	const commandFeedback = $derived(
		form?.control && form.control !== 'stop' && form.control !== 'recover' ? form : null
	);
	const recoveryFeedback = $derived(form?.control === 'recover' ? form : null);

	// What may be offered, decided from the machine the operator is looking at.
	const plan = $derived(controlsPlan(machine ?? null, data.capabilities));
	const recovery = $derived(recoveryPlan(machine ?? null, data.capabilities));

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

	<StopControl csrfToken={data.csrfToken} error={stopFeedback?.error} {accepted} />

	<MachineStatus snapshot={machine} {unavailable} stale={view.stale} refreshing={view.refreshing} />

	<RecoveryControl
		plan={recovery}
		csrfToken={data.csrfToken}
		recoveryKey={data.recoveryKey ?? null}
		feedback={recoveryFeedback}
	/>

	<ManualControls
		{plan}
		csrfToken={data.csrfToken}
		keys={data.commandKeys ?? null}
		feedback={commandFeedback}
	/>

	<p><a href={resolve('/operations')}>Operation history</a></p>
	<p><a href={resolve('/system')}>System</a></p>
	<p><a href={resolve('/dataset')}>Dataset</a></p>

	<section aria-labelledby="permitted">
		<h2 id="permitted">Permitted for this account</h2>
		<p>Signed in as {data.username} ({data.role}).</p>
		<ul>
			{#each data.capabilities as capability (capability)}
				<li>{LABELS[capability] ?? capability}</li>
			{/each}
		</ul>
	</section>

	<form method="POST" action="/logout">
		<!-- The server refuses this post without the token it issued. -->
		<input type="hidden" name="csrf_token" value={data.csrfToken} />
		<button type="submit">Sign out</button>
	</form>
</main>

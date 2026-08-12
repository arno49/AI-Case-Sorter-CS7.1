<script lang="ts">
	import { onMount } from 'svelte';
	import { invalidateAll } from '$app/navigation';

	import { MachineView } from '$lib/machine-view.svelte';

	let { data, form } = $props();

	/**
	 * The machine as this page believes it to be.
	 *
	 * Reading a snapshot is re-running this page's server load, which already
	 * authorizes the request and translates the daemon's answer. Adding a second
	 * way for the browser to read the machine would be a second thing to keep
	 * honest.
	 */
	const view = new MachineView<typeof data.snapshot & object, { generation: number }>(async () => {
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

		{#if view.stale}
			<!-- The page owes itself a snapshot. Saying so is the point: a screen
			     that is behind must not look like one that is current. -->
			<p role="status">
				This view may be out of date. Reading the machine{view.refreshing ? '…' : ''}
			</p>
		{/if}
		{#if unavailable}
			<p role="alert">{unavailable}</p>
		{:else if machine}
			<dl>
				<dt>Connection</dt>
				<dd>{machine.connection_state}</dd>
				<dt>Faults</dt>
				<dd>{machine.fault_state} ({machine.faults?.length ?? 0} recorded)</dd>
				<dt>Ready</dt>
				<dd>
					{machine.ready ? 'yes' : 'no'}
					{#if machine.readiness_reason}
						— {machine.readiness_reason}
					{/if}
				</dd>
				<dt>Snapshot generation</dt>
				<dd>{machine.generation}</dd>
				<dt>Active operation</dt>
				<dd>
					{#if machine.active_operation}
						{machine.active_operation.type} — {machine.active_operation.state}
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

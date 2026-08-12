/**
 * The rule that keeps a watched screen honest.
 *
 * Every case below is a way the stream can lie by omission — an event out of
 * order, a gap the server admitted to, a daemon that stopped answering — and
 * what the browser is supposed to do about it, which is read rather than guess.
 */

import { describe, expect, it } from 'vitest';

import { MachineView } from './machine-view.svelte';

interface Snapshot {
	readonly generation: number;
	readonly connection_state: string;
}

function snapshot(generation: number, connection_state = 'READY'): Snapshot {
	return { generation, connection_state };
}

/** A snapshot source a spec can count. The last answer repeats. */
function reads(...answers: (Snapshot | null)[]) {
	let taken = 0;
	return {
		get count(): number {
			return taken;
		},
		read: async (): Promise<Snapshot | null> => {
			const answer = answers[Math.min(taken, answers.length - 1)];
			taken += 1;
			return answer ?? null;
		}
	};
}

/** Wait until no read is in flight, which is when the screen is settled. */
async function settled(view: MachineView<Snapshot, { generation: number }>): Promise<void> {
	while (view.refreshing) {
		await new Promise((resolve) => setTimeout(resolve, 0));
	}
}

describe('an event arriving', () => {
	it('is ignored when the snapshot on screen already covers its generation', async () => {
		const source = reads(snapshot(9));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		view.received({ generation: 8 });
		await Promise.resolve();

		expect(source.count).toBe(0);
	});

	it('sends the browser to read a snapshot when the machine has moved on', async () => {
		const source = reads(snapshot(10));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		view.received({ generation: 10 });
		await settled(view);

		expect(view.snapshot).toEqual(snapshot(10));
	});

	it('never builds a machine state out of an event', async () => {
		// The event says something changed. What changed is what the snapshot says,
		// and until one is read the screen keeps the state it can account for.
		const source = reads(null);
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		view.received({ generation: 40 });
		await settled(view);

		expect(view.snapshot).toEqual(snapshot(9));
	});
});

describe('a resynchronisation notice', () => {
	it('reads a snapshot before anything incremental is presented again', async () => {
		const source = reads(snapshot(20));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		await view.resynchronise();

		expect(view.snapshot).toEqual(snapshot(20));
		expect(view.stale).toBe(false);
	});

	it('leaves the screen marked as owing a snapshot until a read succeeds', async () => {
		const source = reads(null);
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		await view.resynchronise();

		expect(view.stale).toBe(true);
	});

	it('recovers on the next attempt, without having shown a state it could not account for', async () => {
		const source = reads(null, snapshot(21));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));
		await view.resynchronise();
		expect(view.snapshot).toEqual(snapshot(9));

		await view.resynchronise();

		expect(view.snapshot).toEqual(snapshot(21));
	});
});

describe('a busy machine', () => {
	it('coalesces a burst of events into a read that ends after the last one', async () => {
		// One read per event would turn a busy machine into load this page inflicted
		// on itself. What matters is that a read ends after the last event.
		const source = reads(snapshot(30));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		for (let generation = 10; generation <= 200; generation += 1) {
			view.received({ generation });
		}
		await settled(view);

		expect(source.count).toBeLessThan(10);
	});

	it('ends up at the snapshot, however many events went past on the way', async () => {
		const source = reads(snapshot(200));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		for (let generation = 10; generation <= 200; generation += 1) {
			view.received({ generation });
		}
		await settled(view);

		expect(view.snapshot).toEqual(snapshot(200));
	});
});

describe('a daemon that stopped answering', () => {
	it('says what the server said, and marks the screen as not to be trusted', async () => {
		const source = reads(snapshot(9));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));

		view.disconnected('The machine service is not answering. The machine was not commanded.');

		expect(view.unavailable).toContain('not answering');
		expect(view.stale).toBe(true);
	});

	it('clears that only when a snapshot has actually been read', async () => {
		const source = reads(snapshot(11));
		const view = new MachineView<Snapshot, { generation: number }>(source.read, snapshot(9));
		view.disconnected('not answering');

		await view.resynchronise();

		expect(view.unavailable).toBeNull();
		expect(view.stale).toBe(false);
	});
});

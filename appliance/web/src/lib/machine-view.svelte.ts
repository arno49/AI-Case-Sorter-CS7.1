/**
 * What the browser believes about the machine, and when it stops believing it.
 *
 * The stream is a prompt, not a source of truth. Nothing here builds a machine
 * state out of events: an event says the machine has moved on from the
 * generation on screen, and the answer to that is to read a snapshot, which is
 * the only thing that describes the machine completely. So the screen is either
 * a snapshot or a snapshot being replaced — never a state assembled from
 * whichever half of a sequence happened to arrive.
 *
 * That is what makes the appliance safe to watch over a connection that drops.
 * A gap costs a read, not a wrong picture of a machine that can move.
 */

/** All this needs of a snapshot: which generation it describes. */
export interface Snapshotted {
	readonly generation: number;
}

/** All this needs of an event: which generation it belongs to. */
export interface Generational {
	readonly generation: number;
}

export class MachineView<S extends Snapshotted, E extends Generational> {
	/** The machine as last read. `null` until a read succeeds. */
	snapshot = $state<S | null>(null);
	/** A read is in flight; what is on screen may be a moment behind. */
	refreshing = $state(false);
	/** Why the machine cannot be reached, in the words the server chose. */
	unavailable = $state<string | null>(null);
	/** A snapshot is owed and has not been read: do not trust what is shown. */
	stale = $state(false);

	#reading = false;
	#again = false;

	constructor(
		private readonly readSnapshot: () => Promise<S | null>,
		initial: S | null = null
	) {
		this.snapshot = initial;
	}

	/**
	 * One event from the stream.
	 *
	 * An event that belongs to a generation the snapshot already covers is
	 * nothing to act on. Anything newer means the screen is behind, and being
	 * behind is resolved by reading, not by guessing what the event implies.
	 */
	received(event: E): void {
		if (this.snapshot !== null && event.generation <= this.snapshot.generation) {
			return;
		}
		void this.#refresh();
	}

	/**
	 * The server said this browser may have missed something.
	 *
	 * Until the read that follows succeeds, the screen is marked as owing a
	 * snapshot, so a stale picture is visibly stale rather than quietly wrong.
	 */
	async resynchronise(): Promise<void> {
		this.stale = true;
		await this.#refresh();
	}

	/** The server cannot reach the daemon. Say so; do not invent a state. */
	disconnected(message: string): void {
		this.unavailable = message;
		this.stale = true;
	}

	/**
	 * Read a snapshot, coalescing whatever arrives while reading.
	 *
	 * A busy machine can produce events far faster than a page can read, and one
	 * read per event would turn a busy machine into a self-inflicted load. What
	 * matters is that a read *ends* after the last event, not that each one has
	 * its own.
	 */
	async #refresh(): Promise<void> {
		if (this.#reading) {
			this.#again = true;
			return;
		}
		this.#reading = true;
		this.refreshing = true;
		try {
			do {
				this.#again = false;
				const snapshot = await this.readSnapshot();
				if (snapshot !== null) {
					this.snapshot = snapshot;
					this.unavailable = null;
					this.stale = false;
				}
			} while (this.#again);
		} catch {
			// The read failed. The screen keeps saying it owes a snapshot, which is
			// the true thing to say, and the next event or reconnection tries again.
		} finally {
			this.#reading = false;
			this.refreshing = false;
		}
	}
}

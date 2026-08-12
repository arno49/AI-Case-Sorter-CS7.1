/**
 * The browser's view of the machine, as it changes.
 *
 * This is a one-way stream of what the daemon already decided. Nothing here
 * commands anything, so the only authority it needs is the one that lets an
 * account see the machine at all — and it asks for that here, next to the
 * effect, rather than trusting that a page rendered.
 *
 * The browser's own `EventSource` reconnects on its own and sends back the last
 * `id:` it saw. That is the daemon's `event_id`, unrenumbered, so a resumption
 * asks for exactly the position the daemon would understand. Only real events
 * carry an `id:`; a resynchronisation notice must not become a cursor, or the
 * browser's next reconnection would name a position that never existed.
 */

import type { RequestHandler } from '@sveltejs/kit';

import { requireCapability } from '$lib/server/auth/authorization';
import type { BrowserMessage } from '$lib/server/daemon/broadcast';
import { webRuntime } from '$lib/server/runtime';

/**
 * How often the connection says something when the machine does not.
 *
 * The daemon has its own heartbeat upstream; this one is for the hop in front of
 * it, where a reverse proxy that sees nothing for long enough will close an idle
 * connection. A comment frame is not an event and does not move the cursor.
 */
const BROWSER_HEARTBEAT_MS = 15_000;

const KEEP_ALIVE = ': keep-alive\n\n';

export const GET: RequestHandler = ({ locals, request }) => {
	requireCapability(locals.user, 'machine.read');

	const { events } = webRuntime();
	const closed = new AbortController();
	const messages = events.subscribe({
		from: cursorFrom(request.headers.get('last-event-id')),
		signal: closed.signal
	});

	const stream = new ReadableStream<Uint8Array>({
		start(controller) {
			void pump(controller, messages, closed);
		},
		cancel() {
			closed.abort();
		}
	});

	return new Response(stream, {
		headers: {
			'content-type': 'text/event-stream',
			'cache-control': 'no-store',
			// The appliance is fronted by a reverse proxy; buffering this response
			// would hold events until the buffer filled, which for a stream that is
			// quiet most of the time means holding them indefinitely.
			'x-accel-buffering': 'no'
		}
	});
};

async function pump(
	controller: ReadableStreamDefaultController<Uint8Array>,
	messages: AsyncGenerator<BrowserMessage, void, void>,
	closed: AbortController
): Promise<void> {
	const encoder = new TextEncoder();
	const write = (frame: string) => controller.enqueue(encoder.encode(frame));
	const heartbeat = setInterval(() => {
		try {
			write(KEEP_ALIVE);
		} catch {
			// The browser is gone; the loop below is about to find that out too.
			closed.abort();
		}
	}, BROWSER_HEARTBEAT_MS);

	try {
		for await (const message of messages) {
			write(frameFor(message));
		}
	} catch {
		// A browser that disappeared mid-write is not an error worth reporting: it
		// is the ordinary end of a page. Nothing was commanded, so nothing is owed.
	} finally {
		clearInterval(heartbeat);
		closed.abort();
		void messages.return();
		try {
			controller.close();
		} catch {
			// Already closed by the browser going away.
		}
	}
}

/**
 * `Last-Event-ID` as the daemon would read it.
 *
 * A cursor this cannot parse is treated as no cursor at all, which starts the
 * browser at a resynchronisation. Guessing at a malformed one would be a way to
 * resume from a position nobody chose.
 */
function cursorFrom(header: string | null): number | undefined {
	if (header === null) {
		return undefined;
	}
	const cursor = Number(header);
	return Number.isSafeInteger(cursor) && cursor >= 0 ? cursor : undefined;
}

function frameFor(message: BrowserMessage): string {
	if (message.kind !== 'event') {
		return `event: ${message.kind}\ndata: ${JSON.stringify(message)}\n\n`;
	}
	const { event } = message;
	return (
		`id: ${event.event_id}\n` +
		`event: ${fieldValue(event.type)}\n` +
		`data: ${JSON.stringify(event)}\n\n`
	);
}

/**
 * A value that cannot end the field it is written into.
 *
 * The event body is JSON, which escapes its own newlines, but the `event:` name
 * is written raw. A newline there would let a daemon that has gone wrong compose
 * a second frame inside the first.
 */
function fieldValue(value: string): string {
	return value.replace(/[\r\n]/g, ' ');
}

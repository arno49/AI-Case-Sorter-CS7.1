/**
 * Reading a rendered page the way a keyboard does.
 *
 * The specs that use this render a real component with `svelte/server` and then
 * ask what an operator would find: which controls are reachable, in what order,
 * what they are called, and which form a control would submit.
 *
 * What this is and is not. It is a reader over HTML this project produced
 * itself, not a browser and not a general parser — it does not compute layout,
 * run script, or resolve the full accessible-name algorithm, so it cannot show
 * that a control is visible or that focus is not moved by script. What it does
 * show is the part that decides whether a keyboard can reach the stop at all:
 * document order, the absence of anything that reorders it, and the form the
 * control belongs to. Anything beyond that is a hardware- or browser-level
 * claim and is not made here.
 */

const COMMENT = /<!--[\s\S]*?-->/g;
const TAG = /<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|'[^']*'|[^>])*)>/g;
const ATTRIBUTE = /([a-zA-Z_:@][\w:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;

/** A form as the browser would submit it. */
export interface RenderedForm {
	readonly action: string;
	readonly method: string;
	/**
	 * What a submitted body carries: hidden and pre-filled inputs, each enabled
	 * select's chosen option — the one marked `selected`, else the first — and
	 * a checkbox or radio only when it is marked `checked`, which is what a
	 * browser submits untouched.
	 */
	readonly fields: Readonly<Record<string, string>>;
}

/** One control a keyboard can land on. */
export interface Focusable {
	readonly tag: string;
	readonly type: string | null;
	readonly id: string | null;
	/** The name an operator hears or reads: `aria-label`, else its own text. */
	readonly name: string;
	readonly disabled: boolean;
	/** `null` when the element declares none, which is the ordinary case. */
	readonly tabIndex: number | null;
	/** The form this control would submit, when it is inside one. */
	readonly form: RenderedForm | null;
}

interface OpenElement {
	readonly tag: string;
	readonly attributes: Readonly<Record<string, string>>;
	readonly textFrom: number;
	readonly index: number;
}

/**
 * Every focusable control, in the order a keyboard reaches them.
 *
 * That order is document order, which holds only while nothing declares a
 * positive `tabindex`. `focusOrderIsDocumentOrder` is the assertion that says
 * so; this function does not silently reorder to hide a page that broke it.
 */
export function focusOrder(html: string): readonly Focusable[] {
	const source = html.replace(COMMENT, '');
	const found: Focusable[] = [];
	const forms: { attributes: Readonly<Record<string, string>>; fields: Record<string, string> }[] =
		[];
	const open: OpenElement[] = [];
	// The select whose options are being read, when one is open. Selects do not
	// nest, so one slot is the whole state.
	let select: { name: string; chosen: string | null; sawSelected: boolean } | null = null;

	TAG.lastIndex = 0;
	for (let match = TAG.exec(source); match !== null; match = TAG.exec(source)) {
		const [whole, closing, rawTag] = match;
		const tag = rawTag.toLowerCase();

		if (closing === '/') {
			if (tag === 'form') {
				forms.pop();
			}
			if (tag === 'select' && select !== null) {
				if (forms.length > 0) {
					forms[forms.length - 1].fields[select.name] = select.chosen ?? '';
				}
				select = null;
			}
			// Pop only the element this tag closes: a closing tag of something
			// that was never pushed must not swallow an open control's name.
			const started = open[open.length - 1];
			if (started !== undefined && started.tag === tag) {
				open.pop();
				found[started.index] = {
					...found[started.index],
					name: accessibleName(started.attributes, source.slice(started.textFrom, match.index))
				};
			}
			continue;
		}

		const attributes = attributesOf(match[3] ?? '');
		if (tag === 'form') {
			forms.push({ attributes, fields: {} });
			continue;
		}
		if (tag === 'input') {
			const name = attributes.name;
			const type = (attributes.type ?? 'text').toLowerCase();
			// An unchecked checkbox or radio submits nothing at all; a browser
			// does not send a field a person never selected.
			const submits = (type !== 'checkbox' && type !== 'radio') || 'checked' in attributes;
			if (name !== undefined && submits && forms.length > 0) {
				forms[forms.length - 1].fields[name] = attributes.value ?? '';
			}
		}
		if (tag === 'select') {
			const name = attributes.name;
			// A disabled control submits nothing, so nothing is gathered for it.
			if (name !== undefined && !('disabled' in attributes)) {
				select = { name, chosen: null, sawSelected: false };
			}
		}
		if (tag === 'option' && select !== null) {
			// This reader is over HTML this project writes, and its options always
			// carry an explicit `value`.
			const value = attributes.value ?? '';
			if ('selected' in attributes && !select.sawSelected) {
				select.chosen = value;
				select.sawSelected = true;
			} else if (!select.sawSelected && select.chosen === null) {
				select.chosen = value;
			}
		}
		if (!isFocusable(tag, attributes)) {
			continue;
		}

		const control: Focusable = {
			tag,
			type: attributes.type ?? null,
			id: attributes.id ?? null,
			name: accessibleName(attributes, ''),
			disabled: 'disabled' in attributes,
			tabIndex: tabIndexOf(attributes),
			form: formFor(forms)
		};
		found.push(control);
		// A void element has no text to gather; anything else gets its name from
		// what it wraps, once the closing tag is reached.
		if (tag !== 'input') {
			open.push({
				tag,
				attributes,
				textFrom: match.index + whole.length,
				index: found.length - 1
			});
		}
	}

	return found;
}

/**
 * Whether document order is the tab order.
 *
 * A positive `tabindex` anywhere on the page pulls that element in front of
 * everything that declares none, so a control that reads as first can be
 * reached second. A page that wants its stop control found first must not
 * contain one at all.
 */
export function focusOrderIsDocumentOrder(html: string): boolean {
	return focusOrder(html).every((control) => control.tabIndex === null || control.tabIndex <= 0);
}

/** The first reachable control whose name matches, or `undefined`. */
export function firstFocusable(
	html: string,
	matching: RegExp
): (Focusable & { readonly position: number }) | undefined {
	const order = focusOrder(html);
	const position = order.findIndex(
		(control) => !control.disabled && control.tabIndex !== -1 && matching.test(control.name)
	);
	return position === -1 ? undefined : { ...order[position], position };
}

/** The text of the elements a spec names by `data-field`. */
export function fieldText(html: string, field: string): readonly string[] {
	const source = html.replace(COMMENT, '');
	const marker = `data-field="${field}"`;
	const texts: string[] = [];
	for (
		let at = source.indexOf(marker);
		at !== -1;
		at = source.indexOf(marker, at + marker.length)
	) {
		const start = source.lastIndexOf('<', at);
		const tag = /^<([a-zA-Z][\w-]*)/.exec(source.slice(start, at))?.[1];
		const end = source.indexOf('>', at);
		if (tag === undefined || end === -1) {
			continue;
		}
		texts.push(textOf(source.slice(end + 1, closingOf(source, tag, end + 1))));
	}
	return texts;
}

/** All the text a page shows, with the markup taken out. */
export function visibleText(html: string): string {
	return textOf(html.replace(COMMENT, ''));
}

function closingOf(source: string, tag: string, from: number): number {
	const pattern = new RegExp(`<(/?)${tag}\\b`, 'g');
	pattern.lastIndex = from;
	let depth = 0;
	for (let match = pattern.exec(source); match !== null; match = pattern.exec(source)) {
		if (match[1] === '/') {
			if (depth === 0) {
				return match.index;
			}
			depth -= 1;
		} else {
			depth += 1;
		}
	}
	return source.length;
}

function isFocusable(tag: string, attributes: Readonly<Record<string, string>>): boolean {
	if (tag === 'a') {
		return 'href' in attributes;
	}
	if (tag === 'input') {
		return (attributes.type ?? 'text').toLowerCase() !== 'hidden';
	}
	if (tag === 'button' || tag === 'select' || tag === 'textarea') {
		return true;
	}
	return 'tabindex' in attributes;
}

function tabIndexOf(attributes: Readonly<Record<string, string>>): number | null {
	const declared = attributes.tabindex;
	if (declared === undefined) {
		return null;
	}
	const value = Number(declared);
	return Number.isSafeInteger(value) ? value : null;
}

function formFor(
	forms: readonly { attributes: Readonly<Record<string, string>>; fields: Record<string, string> }[]
): RenderedForm | null {
	const innermost = forms[forms.length - 1];
	if (innermost === undefined) {
		return null;
	}
	return {
		action: innermost.attributes.action ?? '',
		method: (innermost.attributes.method ?? 'GET').toUpperCase(),
		fields: innermost.fields
	};
}

function accessibleName(attributes: Readonly<Record<string, string>>, text: string): string {
	return attributes['aria-label'] ?? (textOf(text) || (attributes.value ?? ''));
}

function attributesOf(raw: string): Readonly<Record<string, string>> {
	const attributes: Record<string, string> = {};
	ATTRIBUTE.lastIndex = 0;
	for (let match = ATTRIBUTE.exec(raw); match !== null; match = ATTRIBUTE.exec(raw)) {
		attributes[match[1].toLowerCase()] = match[2] ?? match[3] ?? match[4] ?? '';
	}
	return attributes;
}

const ENTITIES: Readonly<Record<string, string>> = Object.freeze({
	amp: '&',
	lt: '<',
	gt: '>',
	quot: '"',
	'#39': "'"
});

function textOf(html: string): string {
	return html
		.replace(COMMENT, '')
		.replace(/<[^>]*>/g, ' ')
		.replace(/&(#?\w+);/g, (whole, name: string) => ENTITIES[name] ?? whole)
		.replace(/\s+/g, ' ')
		.trim();
}

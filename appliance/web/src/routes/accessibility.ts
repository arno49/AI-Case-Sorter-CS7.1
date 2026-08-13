/**
 * Automated WCAG 2.1/2.2 A/AA checks against this project's own rendered
 * pages (PI-SWQ-002).
 *
 * A genuinely different, stronger claim than `rendered.ts`'s own regex
 * reader, and a genuinely different limit: axe-core runs inside a fresh
 * jsdom window per check (loaded as a real `<script>`, never sharing a
 * Node-process global with another check - reusing one `axe` instance
 * across documents corrupts its internal state), but jsdom computes no CSS
 * layout or paint. Rendering-dependent rules - `color-contrast` chief
 * among them - report "incomplete" here, not pass or fail, and stay part
 * of this task's own manual assistive-technology review, never claimed as
 * automated evidence.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const AXE_SOURCE = readFileSync(fileURLToPath(import.meta.resolve('axe-core/axe.min.js')), 'utf8');

/** The WCAG 2.2 AA rule set this project targets (`testing-and-quality.md`). */
const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'];

/**
 * Rules that belong to the page shell (`src/app.html`), not any page
 * component this checks: `render()` only ever returns one component's own
 * body fragment, wrapped here in a bare `<html>`/`<body>` just so axe has
 * somewhere to run - `<title>`/`lang` are the static shell's job, checked
 * once there rather than flagged as a false "violation" on every page.
 */
const SHELL_RULES = ['document-title', 'html-has-lang'];

export interface AccessibilityViolation {
	readonly id: string;
	readonly impact: string | null;
	readonly help: string;
	readonly nodeCount: number;
}

export interface AccessibilityReport {
	readonly violations: readonly AccessibilityViolation[];
	/** Rules axe could not fully evaluate against a layout-less jsdom document. */
	readonly incompleteRuleIds: readonly string[];
}

/** Run axe-core's WCAG 2.1/2.2 A/AA rules against one rendered page's HTML. */
export async function checkAccessibility(bodyHtml: string): Promise<AccessibilityReport> {
	const dom = new JSDOM(`<!doctype html><html lang="en"><body>${bodyHtml}</body></html>`, {
		url: 'http://localhost/',
		runScripts: 'dangerously'
	});
	try {
		dom.window.eval(AXE_SOURCE);
		const axe = (dom.window as unknown as { axe: typeof import('axe-core') }).axe;
		const results = await axe.run(dom.window.document, {
			runOnly: { type: 'tag', values: WCAG_TAGS },
			rules: Object.fromEntries(SHELL_RULES.map((id) => [id, { enabled: false }]))
		});
		return {
			violations: results.violations.map((violation) => ({
				id: violation.id,
				impact: violation.impact ?? null,
				help: violation.help,
				nodeCount: violation.nodes.length
			})),
			incompleteRuleIds: results.incomplete.map((entry) => entry.id)
		};
	} finally {
		dom.window.close();
	}
}

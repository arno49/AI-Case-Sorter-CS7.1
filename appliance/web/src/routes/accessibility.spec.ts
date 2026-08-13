import { describe, expect, it } from 'vitest';

import { checkAccessibility } from './accessibility';

describe('checkAccessibility', () => {
	it('reports a real violation, so this is not a check that always passes', async () => {
		const report = await checkAccessibility('<img src="x.png">');

		expect(report.violations.map((violation) => violation.id)).toContain('image-alt');
	});

	it('reports nothing for a page with no real problem', async () => {
		const report = await checkAccessibility(
			'<button type="button">Stop the machine</button><p>All clear.</p>'
		);

		expect(report.violations).toEqual([]);
	});

	it('never flags document-title or html-has-lang - the page shells job, not this fragment', async () => {
		// render() only ever returns one component's own body; the checker
		// wraps it in a bare shell, which must not itself fail the check.
		const report = await checkAccessibility('<p>fragment only, no head</p>');

		expect(report.violations.map((violation) => violation.id)).not.toContain('document-title');
		expect(report.violations.map((violation) => violation.id)).not.toContain('html-has-lang');
	});

	it('checks two different pages independently, without state leaking between them', async () => {
		// axe-core corrupts its own state if one instance is reused across
		// documents - this proves each check runs in its own isolated jsdom
		// window, loading axe fresh every time.
		const broken = await checkAccessibility('<img src="x.png">');
		const clean = await checkAccessibility('<img src="x.png" alt="a real photo">');

		expect(broken.violations.map((violation) => violation.id)).toContain('image-alt');
		expect(clean.violations).toEqual([]);
	});
});

import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';

import Page from './+page.svelte';
import { checkAccessibility } from '../accessibility';

interface PageData {
	readonly csrfToken: string;
	readonly notice: string | null;
}

function rendered(data: PageData, form: unknown = null): string {
	return render(Page as never, { props: { data, form } as never }).body;
}

describe('automated accessibility checks (PI-SWQ-002)', () => {
	it('meets WCAG 2.1/2.2 A/AA rules with no notice or error shown', async () => {
		const html = rendered({ csrfToken: 'csrf', notice: null });

		const report = await checkAccessibility(html);

		expect(report.violations).toEqual([]);
	});

	it('meets WCAG 2.1/2.2 A/AA rules with a notice and a form error shown', async () => {
		const html = rendered(
			{ csrfToken: 'csrf', notice: 'Your session ended.' },
			{ error: 'That username or password was not recognized.' }
		);

		const report = await checkAccessibility(html);

		expect(report.violations).toEqual([]);
	});
});

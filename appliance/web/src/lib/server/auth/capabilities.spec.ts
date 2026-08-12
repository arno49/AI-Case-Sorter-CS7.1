/**
 * The RBAC matrix, asserted pair by pair.
 *
 * The table below is transcribed from
 * `docs/architecture/security-and-safety.md` by hand rather than derived from
 * the implementation. A test that computed the expectation from `GRANTS` would
 * agree with any mistake in it.
 */

import { describe, expect, it } from 'vitest';

import { CAPABILITIES, can, capabilitiesFor, type Capability } from './capabilities';
import { ROLES, type Role } from './users';

interface MatrixRow {
	readonly capability: Capability;
	readonly viewer: boolean;
	readonly operator: boolean;
	readonly administrator: boolean;
}

const MATRIX: readonly MatrixRow[] = [
	{ capability: 'machine.read', viewer: true, operator: true, administrator: true },
	{ capability: 'machine.stop', viewer: true, operator: true, administrator: true },
	{ capability: 'machine.operate', viewer: false, operator: true, administrator: true },
	{ capability: 'machine.recover', viewer: false, operator: false, administrator: true },
	{ capability: 'config.write', viewer: false, operator: false, administrator: true },
	{ capability: 'users.manage', viewer: false, operator: false, administrator: true },
	{ capability: 'protocol.direct', viewer: false, operator: false, administrator: false }
];

const PAIRS = MATRIX.flatMap((row) =>
	ROLES.map((role) => ({ role, capability: row.capability, allowed: row[role] }))
);

describe('the documented matrix', () => {
	it.each(PAIRS)('answers $allowed for $role and $capability', ({ role, capability, allowed }) => {
		expect(can(role, capability)).toBe(allowed);
	});

	it('names every capability the code defines', () => {
		// A capability added to the code and not to this table would otherwise go
		// unasserted for every role.
		expect(MATRIX.map((row) => row.capability)).toEqual([...CAPABILITIES]);
	});

	it('lets a viewer stop the machine', () => {
		// Withholding the stop from the least privileged account would make an
		// access-control table into a safety decision.
		expect(can('viewer', 'machine.stop')).toBe(true);
	});

	it('lets no role drive the protocol or the device path', () => {
		expect(ROLES.filter((role) => can(role, 'protocol.direct'))).toEqual([]);
	});

	it('gives each role everything the role below it has', () => {
		const held = (role: Role) => new Set(capabilitiesFor(role));
		for (const [lower, higher] of [
			['viewer', 'operator'],
			['operator', 'administrator']
		] as const) {
			expect([...held(lower)].every((capability) => held(higher).has(capability))).toBe(true);
		}
	});
});

describe('what a page may be told', () => {
	it('reports a role in matrix order', () => {
		expect(capabilitiesFor('operator')).toEqual([
			'machine.read',
			'machine.stop',
			'machine.operate'
		]);
	});

	it('never reports a capability no role holds', () => {
		for (const role of ROLES) {
			expect(capabilitiesFor(role)).not.toContain('protocol.direct');
		}
	});

	it('gives an administrator everything that is grantable', () => {
		expect(capabilitiesFor('administrator')).toEqual(
			CAPABILITIES.filter((capability) => capability !== 'protocol.direct')
		);
	});
});

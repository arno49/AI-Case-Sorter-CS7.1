/**
 * The RBAC matrix as data.
 *
 * This is a transcription of the table in
 * `docs/architecture/security-and-safety.md`, and the only place a role is
 * turned into permission. Routes ask for a capability, never for a role: a
 * handler that reads `role === 'administrator'` would have to be found and
 * changed again the next time the matrix moves.
 *
 * Three rows of that table deserve their names here rather than a comment
 * elsewhere. A viewer may stop the machine -- withholding the stop from the
 * least privileged account would be a safety decision made by an access-control
 * table. No role at all may drive the protocol or the device path, so
 * `protocol.direct` exists to be refused: a route that ever asks for it is
 * denied for everybody, including an administrator. And `vision.train`
 * (PI-VISION-005) is a deliberate departure from this project's own pattern
 * of reserving impactful actions to `administrator` (ADR-0013): retraining
 * and activating a classifier candidate never touches machine motion, so it
 * is granted at the `operator` row alongside `machine.operate`.
 */

import type { Role } from './users';

export const CAPABILITIES = [
	'machine.read',
	'machine.stop',
	'machine.operate',
	'vision.train',
	'machine.recover',
	'config.write',
	'users.manage',
	'protocol.direct'
] as const;

export type Capability = (typeof CAPABILITIES)[number];

const VIEWER: readonly Capability[] = ['machine.read', 'machine.stop'];

const OPERATOR: readonly Capability[] = [...VIEWER, 'machine.operate', 'vision.train'];

const ADMINISTRATOR: readonly Capability[] = [
	...OPERATOR,
	'machine.recover',
	'config.write',
	'users.manage'
];

/**
 * Each role holds everything the role below it holds. That is what the matrix
 * says; building the sets by extension keeps it true when a capability is added
 * to a middle row.
 */
const GRANTS: Readonly<Record<Role, ReadonlySet<Capability>>> = Object.freeze({
	viewer: new Set(VIEWER),
	operator: new Set(OPERATOR),
	administrator: new Set(ADMINISTRATOR)
});

export function can(role: Role, capability: Capability): boolean {
	return GRANTS[role].has(capability);
}

/**
 * What a role may do, in matrix order.
 *
 * Pages use this to decide what to show. Showing a control is not permission to
 * use it -- every request is authorized again on the server -- so this list is
 * safe to send to a browser.
 */
export function capabilitiesFor(role: Role): readonly Capability[] {
	return CAPABILITIES.filter((capability) => GRANTS[role].has(capability));
}

/**
 * The machine's read model, named once.
 *
 * These are the contract's own types, aliased where both the server and a
 * screen can reach them. Nothing here describes a command: a browser reads the
 * machine and only the server commands it, so the command types stay next to
 * the client that sends them.
 *
 * Because every alias comes from the generated contract, a change to
 * `cs71d-v1.openapi.json` that a screen has not caught up with fails the build
 * rather than a machine.
 */

import type { components } from '$lib/api/cs71d-v1';

type Schemas = components['schemas'];

export type MachineSnapshot = Schemas['MachineSnapshot'];
export type Operation = Schemas['Operation'];
export type OperationPage = Schemas['OperationPage'];
export type OperationState = Schemas['OperationState'];
export type OperationType = Schemas['OperationType'];
export type ConnectionState = Schemas['ConnectionState'];
export type FaultState = Schemas['FaultState'];
export type Fault = Schemas['Fault'];
export type Capabilities = Schemas['Capabilities'];
export type System = Schemas['System'];

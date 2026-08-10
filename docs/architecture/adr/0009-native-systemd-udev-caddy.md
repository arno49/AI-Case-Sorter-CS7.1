# ADR-0009: Native systemd/udev/Caddy deployment, no containers for MVP

**Status:** Accepted<br>
**Date:** 2026-08-10

## Context

USB identity, Unix sockets and recovery need simple native control on a Pi. Containers add device and service lifecycle complexity without MVP benefit.

## Decision

Deploy on Raspberry Pi OS with systemd, udev stable device identity and Caddy reverse proxy. Do not use containers for MVP.

## Options considered

| Option | Reason selected or rejected |
| --- | --- |
| Native systemd/udev/Caddy | Selected: native device/socket/sandbox integration. |
| Containers | Rejected: complicate USB, udev and ownership model. |
| Manual foreground processes | Rejected: lacks restart/order/hardening controls. |

## Consequences

### Positive

- Clear service ordering, logs, limits and filesystem ownership.
- Caddy exposes only SvelteKit HTTPS.

### Negative

- Installer must manage host users, units and upgrades.

## Implementation constraints

- udev match includes approved VID/PID and serial number.
- Linux DTR remains NOT_EXECUTED until dedicated HIL evidence.

## Validation and revisit triggers

- Clean Pi install, reboot, backup/rollback and service hardening tests.
- Revisit containerization only after equivalent device/safety lifecycle evidence.

## Links

- [Deployment](../deployment-and-operations.md); [PI-OPS-001](../backlog.md#pi-ops-001--package-native-pi-services-udev-and-caddy).

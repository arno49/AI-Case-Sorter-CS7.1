# Architecture Decision Records

ADRs record durable architecture decisions for the Raspberry Pi 5 appliance. They use immutable sequence numbers; superseding a decision adds a new ADR rather than editing history. An ADR is Accepted only after review. The template is [template.md](template.md).

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-two-process-modular-monolith.md) | Accepted | Two-process modular monolith |
| [0002](0002-sveltekit-ssr-bff.md) | Accepted | SvelteKit SSR Node.js BFF/UI |
| [0003](0003-python-cs71d-single-serial-owner.md) | Accepted | Python `cs71d` sole serial ownership |
| [0004](0004-internal-http-json-unix-domain-socket.md) | Accepted | HTTP/JSON over Unix domain socket |
| [0005](0005-rest-commands-sse-no-websocket.md) | Accepted | REST commands plus SSE, no MVP WebSocket |
| [0006](0006-fail-closed-priority-stop.md) | Accepted | Fail-closed state and priority software stop |
| [0007](0007-openapi-generated-types.md) | Accepted | OpenAPI and generated TypeScript types |
| [0008](0008-separate-sqlite-ownership.md) | Accepted | Separate SQLite databases and ownership |
| [0009](0009-native-systemd-udev-caddy.md) | Accepted | Native systemd/udev/Caddy deployment |
| [0010](0010-deterministic-simulator-evidence-boundary.md) | Accepted | Deterministic simulator and hardware evidence boundary |
| [0011](0011-local-auth-rbac-sessions.md) | Accepted | Local authentication/RBAC/session model |
| [0012](0012-versioned-operations-events-idempotency.md) | Accepted | Versioned operations/events and idempotency |

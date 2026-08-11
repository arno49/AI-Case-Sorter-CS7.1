# Raspberry Pi appliance workspaces

The appliance is split into two independently deployable processes:

- `daemon/`: Python `cs71d`, the future sole serial owner and private API.
- `web/`: SvelteKit SSR/Node.js browser-facing BFF and operator interface.
- `contracts/`: the executable private OpenAPI v1 contract.

Neither workspace opens a real serial device in its development defaults.
`cs71d` defaults to the simulator backend with no `device_path`; production
configuration accepts only the installer-managed `/dev/cs71` identity.

The daemon workspace includes a deterministic `SIMULATOR_ONLY` protocol
transport with explicit clock advancement. It is software evidence only.

## Daemon

From the repository root in a Python 3.12 virtual environment:

```sh
python -m pip install --require-hashes -r appliance/daemon/requirements-dev.txt
python -m pip install --no-build-isolation -e ./host -e ./appliance/daemon
(cd appliance/daemon && ruff format --check . && ruff check . && mypy && pytest)
python -m build --no-isolation --wheel --sdist appliance/daemon
python -m cs71d.cli --check-config appliance/daemon/config/development.toml
```

## Web

The locked Vite/SvelteKit toolchain requires Node.js 22.12 or newer and npm
10.9 or newer. `.nvmrc` selects the CI-tested Node 22 patch release.

```sh
cd appliance/web
npm ci
npm run check:api
npm run lint
npm run check
npm test
npm run build
```

The Node adapter emits the production SSR bundle under `appliance/web/build/`.
The generated API types under `src/lib/api/` are committed; `check:api` fails if
they drift from the canonical OpenAPI document.

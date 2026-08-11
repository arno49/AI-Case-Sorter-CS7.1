# Raspberry Pi appliance workspaces

The appliance is split into two independently deployable processes:

- `daemon/`: Python `cs71d`, the future sole serial owner and private API.
- `web/`: SvelteKit SSR/Node.js browser-facing BFF and operator interface.
- `contracts/`: the executable private OpenAPI v1 contract.

Neither workspace opens a real serial device in its development defaults.
`cs71d` defaults to the simulator backend with no `device_path`; production
configuration accepts only the installer-managed `/dev/cs71` identity.

## Daemon

From the repository root in a Python 3.12 virtual environment:

```sh
python -m pip install -e ./host -e "./appliance/daemon[dev]" "build==1.2.2.post1"
python -m pytest appliance/daemon/tests
python -m build --wheel --sdist appliance/daemon
python -m cs71d.cli --check-config appliance/daemon/config/development.toml
```

## Web

The locked Vite/SvelteKit toolchain requires Node.js 22.12 or newer and npm
10.9 or newer. `.nvmrc` selects the Node 22 release line.

```sh
cd appliance/web
npm ci
npm run check
npm run build
```

The Node adapter emits the production SSR bundle under `appliance/web/build/`.

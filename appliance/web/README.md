# CS7.1 appliance web

SvelteKit SSR/Node.js workspace for the browser-facing BFF and operator UI. The
browser will communicate only with this service; direct daemon and serial access
are outside this workspace's boundary.

Use Node.js 22.12 or newer and npm 10.9 or newer; `.nvmrc` selects the
CI-tested Node 22 patch release.

## Developing

Install the locked dependencies and start a development server:

```sh
npm ci
npm run dev
```

## Building

Type-check and create the Node SSR production bundle:

```sh
npm run check:api
npm run lint
npm run check
npm test
npm run build
```

`check:api` verifies that committed TypeScript definitions match the canonical
`cs71d` OpenAPI document.

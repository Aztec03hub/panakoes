# @panakoes/admin

Admin dashboard for Panakoes. SvelteKit 2.x SPA, Tailwind 3.x, shadcn-svelte
"Default" theme. Targets static deployment to S3 + CloudFront via the
SvelteKit static adapter.

This is the v0.1 skeleton. Tier 1 of the admin dashboard (read-only health) is
wired against mock JSON; Tiers 2 (cost) and 3 (lifecycle controls + step-up
MFA) ship in follow-up slices.

---

## Tech stack

- **SvelteKit 2.x** with `@sveltejs/adapter-static` (SPA mode, S3 + CloudFront target).
- **TypeScript** with strict mode.
- **Tailwind 3.x** + **shadcn-svelte** ("Default" theme HSL tokens).
- **Vitest** + **@testing-library/svelte** for unit and component tests.
- **Biome** for lint and format (matching `services/auth/biome.json`).
- **pnpm 11.0.8** as the package manager.

## Layout

```
services/admin/
  src/
    app.html              SvelteKit document shell
    app.css               Tailwind imports + shadcn theme tokens
    lib/
      api.ts              Typed fetch wrappers (tested)
      auth.ts             Better-Auth client stub (placeholder)
      types.ts            Mirror of panakoes-models Python types
      utils.ts            cn() class composer + formatTimestamp (tested)
      components/
        ui/               shadcn-svelte primitives (button, card, badge, table)
        health-badge.svelte
        service-health-card.svelte
    routes/
      +layout.svelte      Top nav, breadcrumb, sign-out button
      +layout.ts          prerender + SSR config
      +page.svelte        Redirect to /dashboard
      +error.svelte       Friendly error page
      login/+page.svelte  Placeholder sign-in form
      dashboard/+page.svelte           Tier 1 read-only health grid (11 services)
      dashboard/[service]/+page.svelte Service detail (logs, errors, metrics; mocked)
  static/
    dashboard/
      health.json         Mock snapshot for the 11 services
      auth.json           Mock detail payload (sample)
      transcriber-stream.json   Mock detail payload (sample, unhealthy)
  tests/
    api.test.ts           Unit tests for lib/api.ts
    utils.test.ts         Unit tests for lib/utils.ts
    service-health-card.test.ts   Component tests for the dashboard card
    setup.ts              jest-dom matchers
```

## Getting started

```bash
# from services/admin
pnpm install
pnpm approve-builds --all   # one-time, accepts the postinstall scripts
                            # (esbuild, biome) recorded in pnpm-workspace.yaml
pnpm dev                    # http://localhost:5173
```

### Available scripts

| Command | Description |
|---|---|
| `pnpm dev` | Vite dev server with HMR |
| `pnpm build` | Static SPA build to `build/` |
| `pnpm preview` | Preview the production build locally |
| `pnpm test` | Vitest run (single pass) |
| `pnpm test:watch` | Vitest watch mode |
| `pnpm test:coverage` | Vitest with v8 coverage; 70% threshold |
| `pnpm lint` | Biome check |
| `pnpm lint:fix` | Biome check with auto-fix |
| `pnpm format` | Biome format write |
| `pnpm typecheck` | svelte-check + tsc |

## Mock health data (v0.1 only)

The dashboard currently fetches from `/dashboard/health.json`, which is a
static asset under `static/dashboard/`. This is intentional: the
health-aggregator service does not exist yet (slice 4 backlog). Once it
ships, swap the URL constant in `src/lib/api.ts` (`HEALTH_ENDPOINT`) for the
live endpoint and add the auth header from `lib/auth.ts`.

The 11 monitored services match the canonical service list from the IAM
module (`infra/dev/iam/`):

- `auth`
- `ingestion-api`
- `summarization`
- `notification`
- `query-api`
- `session-manager`
- `gpu-spawner`
- `transcriber-batch`
- `transcriber-stream`
- `event-router`
- `billing`

Service detail pages read from `/dashboard/<service>.json`. Two sample mocks
ship today (`auth.json`, `transcriber-stream.json`); add more JSON files
under `static/dashboard/` to populate other services' drill-downs in dev.

## Deployment target

The `build/` output is a fully static SPA, suitable for `aws s3 sync` to
the admin bucket fronted by CloudFront. CloudFront's default-root-object
must point at `index.html`, and the distribution should map 403/404
responses to `/index.html` (200) so client-side routes resolve.

## Coverage gate

70% lines / functions / branches / statements on `src/lib/**/*.ts`. Lower
than the backend's 80% / 100% gates because Svelte component logic is
mostly exercised through integration and e2e tests; unit-coverage chasing
on layout markup is low ROI. Authoritative gates live in `vite.config.ts`.

## Auth (deferred)

`src/lib/auth.ts` is a placeholder. Once the Better-Auth Svelte client
lands, that module exposes `signIn`, `signOut`, `getSession`, and
`isAdmin`. Until then, the login form posts to `/auth/sign-in` but does
not persist the session, and the layout's "Sign Out" button is a no-op.

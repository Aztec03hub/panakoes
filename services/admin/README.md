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
      account/+page.svelte             Self-serve account settings (Stripe Customer Portal entry point)
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

## Environment configuration

The SPA reads its API origin from `VITE_API_BASE_URL` at build time. Vite
inlines every `VITE_*` reference into the static bundle, so the same
build pipeline targets dev, preview, and prod by swapping a single
variable at CI bake.

| Variable | Default | Purpose |
|---|---|---|
| `VITE_API_BASE_URL` | `""` (relative paths) | Origin of the API Gateway in front of cost-api + admin-api + auth. No trailing slash. |
| `VITE_USE_LIVE_HEALTH_AGGREGATOR` | `true` | When true (today's default), health + per-service detail come from the live health-aggregator service. Set explicitly to `false` to route them to the bundled static mocks under `static/dashboard/` (useful for offline development). |
| `VITE_OTEL_EXPORTER_OTLP_ENDPOINT` | `""` (no-op exporter) | OTLP/HTTP traces endpoint for the browser-side OpenTelemetry SDK. When unset, the SDK boots in no-op mode and emits zero network traffic. Set to your local ADOT / OTEL collector (typically `http://localhost:4318/v1/traces`) to see fetch + page-transition + error spans in CloudWatch / X-Ray. |
| `VITE_SERVICE_VERSION` | `0.0.0` | Maps to the `service.version` resource attribute on every span. Wire to the git SHA at CI bake time. |
| `VITE_DEPLOYMENT_ENVIRONMENT` | `dev` | Maps to the `deployment.environment` resource attribute. |

### Observability (OpenTelemetry)

The SPA emits client-side spans for every `apiFetch` call, every SvelteKit
page transition, and every uncaught error / unhandled promise rejection.
Resource attributes follow the Panakoes contract (`service.name=admin`,
`service.namespace=panakoes`, `service.version`, `deployment.environment`)
so the SPA's spans group alongside the Node / Python services in CloudWatch
/ X-Ray. JWT claims (`sub`, `role`) attach as `panakoes.user.id` and
`panakoes.user.role` attributes when a session exists. The raw JWT is
NEVER logged.

The exporter is W3C TraceContext + OTLP/HTTP. By default the SDK is in
no-op mode (the entry chunk does not pull in `sdk-trace-web` /
`exporter-trace-otlp-http`). To exercise the wiring locally, run an OTEL
collector (the ADOT collector default exposes HTTP on
`http://localhost:4318/v1/traces`) and set:

```bash
VITE_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318/v1/traces pnpm dev
```

`src/lib/otel.ts` is the single source of truth for the bootstrap; it is
lazy-imported from the layout `onMount` so the SDK never enters the
critical render path. The Node-side library
[`@panakoes/otel`](../otel-lib-ts/README.md) wires the same resource
contract for Node services (auth, future TS services).

Configuration files:

- `services/admin/.env.example`: documented contract for every supported var.
- `services/admin/.env.development`: gitignored local defaults; auto-loaded by `pnpm dev`.
- `services/admin/src/lib/config.ts`: single source of truth that reads
  the env at module load and exports typed values (`API_BASE_URL`,
  `COST_API_BASE`, `ADMIN_API_BASE`, `AUTH_API_BASE`,
  `USE_LIVE_HEALTH_AGGREGATOR`).

Production builds inject `VITE_API_BASE_URL` at `pnpm build` time, e.g.:

```bash
VITE_API_BASE_URL=https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev \
  pnpm build
```

This URL will switch to the `api.panakoes.com` custom domain once
Route53 + ACM ship for it.

### Per-service URL composition (ADR-038 c+ shape)

| Service | Public path under gateway | Backend mount |
|---|---|---|
| cost-api | `/v1/cost-api/api/v1/cost/<route>` | `/api/v1/cost/<route>` |
| admin-api | `/v1/admin-api/api/v1/admin/<route>` | `/api/v1/admin/<route>` |
| auth | `/v1/auth/<route>` | `/<route>` (Hono root mounts) |

The fetch helpers in `lib/api.ts` compose these by appending the
backend's internal subpath to one of the `*_BASE` constants exported
from `lib/config.ts`. To change which deployment the SPA targets, only
`VITE_API_BASE_URL` needs to flip.

## Mock health data (offline fallback)

The dashboard fetches from `${VITE_API_BASE_URL}/v1/health-aggregator/health`
by default. Set `VITE_USE_LIVE_HEALTH_AGGREGATOR=false` to fall back to
the bundled static asset at `/dashboard/health.json` under
`static/dashboard/`. The mock remains useful for offline local development
and for temporarily isolating the SPA from a degraded aggregator without
a redeploy.

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

## Self-serve billing (Stripe Customer Portal)

The `/account` route surfaces a "Manage subscription" button that POSTs
to `POST /v1/billing/portal-session` and redirects the browser to the
Stripe-hosted Customer Portal. End-users upgrade, downgrade, update
their card, and view past invoices without an admin in the loop.

The portal triggers the same `customer.subscription.*` and `invoice.*`
webhooks the checkout flow already handles, so any changes the user
makes flow back through the billing service's existing webhook handler
without extra SPA wiring. `return_url` is the current page URL; the
billing service validates it against a Panakoes-owned allowlist
(`https://dmaopcm3hnxog.cloudfront.net/*` and `https://panakoes.com/*`)
so the endpoint cannot be abused as an open redirect.

## Coverage gate

70% lines / functions / branches / statements on `src/lib/**/*.ts`. Lower
than the backend's 80% / 100% gates because Svelte component logic is
mostly exercised through integration and e2e tests; unit-coverage chasing
on layout markup is low ROI. Authoritative gates live in `vite.config.ts`.

## Auth flow

Auth state lives in `src/lib/auth.svelte.ts`, a Svelte 5 runes module
that owns the client-side session and exposes the helpers `lib/api.ts`
and the layout consume.

### Sign in

`POST {AUTH_API_BASE}/sign-in` with `{email, password}`. On 200 the
service returns `{token, expiresAt, user: {id, email, role}}`. The
`signIn` helper persists that blob to `localStorage` under the single
key `panakoes-admin-auth` and updates the in-memory `$state` store.
On 401 it throws `AuthError` with status preserved; the login page
renders "Invalid email or password.". On 5xx the page renders
"Auth service unavailable. Try again shortly.".

### Bearer header injection

Every authenticated fetch goes through `apiFetch` in `lib/api.ts`,
which merges `Authorization: Bearer <jwt>` from `bearerHeader()` into
`init.headers`. On a 401 response from any downstream endpoint
`apiFetch` calls `signOut()` and navigates to `/login?from=<current>`
so the user returns to where they were after re-authenticating.

### Route gating

`+layout.svelte` runs an `$effect` on every navigation: if the
current pathname is not in the public-paths set (today: `/login`)
AND `isAuthenticated()` is false, the layout `goto`s
`/login?from=<path>` with `replaceState`. The sign-in handler reads
`?from=` and bounces back on success.

### Session expiry

`isAuthenticated()` is derived from session presence AND
`expiresAt > now()`. An expired session is treated as logged-out and
auto-cleared from both memory and localStorage on the next call.

### Sign out (local + server)

`signOut()` clears the in-memory store + localStorage synchronously
(so the UI never blocks on a network round-trip) and best-effort fires
`POST {AUTH_API_BASE}/sign-out` with the existing bearer header. The
auth service soft-deletes the session row (setting `revoked_at`) and
returns 204; any failure (offline, 5xx, 401-already-invalid) is
swallowed so logout always completes locally. Returns a Promise so
tests can deterministically await the server call; app code is
intentionally not required to.

### Whoami on boot (`GET /auth/me`)

`+layout.svelte` calls `validateSession()` from `onMount` exactly once
per page load. The helper hits `GET {AUTH_API_BASE}/auth/me` with the
hydrated bearer token:

- **200**: the token is still valid. The response's `user` payload
  refreshes the locally stored claims so any server-side role
  promotion / email change shows up without a fresh sign-in.
- **401**: the token has been revoked, expired, or signed with a
  rotated key. `validateSession` calls `signOut()` locally and
  best-effort fires the server revoke; the next navigation hits the
  layout's auth gate and lands on `/login?from=<current>`.
- **5xx / network error**: the cached session is INTENTIONALLY
  preserved. A transient gateway failure should never falsely log the
  user out; the next user-driven request will surface a fresh 401 if
  the token is actually dead.

This closes the gap where a token revoked server-side after the SPA
hydrated from localStorage would silently appear "signed in" until
the JWT's `exp` claim caught up. The price is one extra request on
SPA boot; every subsequent fetch continues to rely on the JWT
directly + the global 401 redirect path in `apiFetch`.

### Storage trade-off (localStorage vs HttpOnly cookies)

The SPA ships as a static SvelteKit bundle on S3 + CloudFront with
no server runtime, so we cannot set an HttpOnly cookie at this tier
without standing up an extra server boundary (CloudFront Function, a
tiny edge auth Worker, or a real SvelteKit server adapter). The JWT
itself is the bearer of authority (auth service issues HS256 JWTs,
not opaque session ids), so persisting it in `localStorage` is a
correct primitive for v0.1.

The cost is that any XSS bug on this origin hands an attacker the
JWT. Mitigations in place: small dependency surface, short `exp`
window on the JWT, and the auth service's `/sign-out` server-side
revoke. Long-term plan (tracked separately): front the SPA with a
CloudFront Function or tiny edge Worker that sets an HttpOnly cookie
at the edge so the JWT never reaches `document` storage.

### Coverage

`src/lib/auth.svelte.ts` is gated to 100% lines, branches, functions,
and statements in `vite.config.ts` per the project-wide convention
that auth paths carry the same coverage bar as billing and audit.

## Testing

### Time-dependent tests

Any test that constructs an `expiresAt` (or any other future/past
timestamp) relative to a fixed point in time MUST also fake the system
clock; otherwise the test becomes a time-bomb that fails when real-time
drifts past the fixture.

Pattern (vitest):

```ts
const FIXED_NOW = Date.parse("2026-05-11T12:00:00Z");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FIXED_NOW));
  // ...then also inject the SUT's clock seam if it has one:
  setClock(() => FIXED_NOW);
});

afterEach(() => {
  resetClock();
  vi.useRealTimers();
});
```

Why both fake timers AND the `setClock` seam? Fake timers cover any code
path that consults `Date.now()` directly (notably module-init
`hydrate()` after a `vi.resetModules()` dynamic re-import, where the
SUT's internal `clock` binding is freshly reset to its default
`() => Date.now()`). The `setClock` seam pins the steady-state path
even if a future refactor swaps out fake-timer plumbing. Use both.

If a test asserts behavior against the real wall clock (e.g. verifying
the `resetClock` default actually delegates to `Date.now`), construct
relative timestamps from `Date.now()` after `resetClock()` so the
assertion stays correct under faked time.

Alternative (avoid): hardcoding `expiresAt: "2099-01-01T00:00:00Z"` works
for "session is valid" fixtures because the date is far enough out, but
breaks for any test that needs `expiresAt` to be close to "now" (e.g.
boundary tests around expiry). Prefer fake timers + a fixed `FIXED_NOW`
anchor.

# services/auth

Auth microservice for Panakoes. Issues short-lived JWTs (HS256, 1-hour expiry) backed by database-managed sessions, and exposes a `/validate` endpoint other services call to confirm a token's session has not been revoked. This is the v0.1 MVP; see [`PLANNING.md`](../../PLANNING.md) ADR-005 for the long-form decision record.

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | no | Liveness; returns `{"status":"ok","service":"auth"}` |
| POST | `/sign-up` | no | Create user + session, returns JWT |
| POST | `/sign-in` | no | Authenticate user, returns JWT |
| POST | `/sign-out` | yes | Revoke the session bound to the bearer token |
| POST | `/validate` | yes | Verify JWT + session freshness; returns `{valid, user}` |
| ANY | `/api/auth/*` | varies | Better-Auth's own handler for built-in flows (e.g. `GET /api/auth/get-session`) |

## Environment variables

| Variable | Required / Default | Description |
|---|---|---|
| GET | `/health` | Liveness; returns `{"status":"ok","service":"auth"}` |
| GET | `/.well-known/jwks.json` | Slice-1 placeholder; returns `{"keys": []}` (HS256 has no public key). Slice 2 surfaces RS256 public keys here per ADR-022. |
| POST | `/sign-up` | Create user + session, returns JWT (with `role` claim) |
| POST | `/sign-in` | Authenticate user, returns JWT (with `role` claim) |
| POST | `/sign-out` | Revoke the session bound to the bearer token |
| POST | `/validate` | Verify JWT + session freshness; returns `{valid, user: {id, email, role}}` |
| POST | `/mfa/enroll` | (admin only) issue a TOTP secret + `otpauth://` provisioning URI. Slice-1 stub: nothing persisted; client keeps the secret until verify. |
| POST | `/mfa/verify` | Validate a 6-digit TOTP code against the supplied secret; on success issue a 5-minute step-up token (`step_up=true`). |
| POST | `/mfa/challenge` | Tier 3 gate. 401 + `WWW-Authenticate: StepUp` if no valid step-up token; 200 if present. |
| `DATABASE_URL` | required | Postgres connection string |
| `AUTH_JWT_SECRET` | required | HS256 signing secret; must be at least 32 bytes |
| `PORT` | `8080` | HTTP listen port |
| `LOG_LEVEL` | `info` | Pino log level |
| `NODE_ENV` | `development` | Environment label |
| `AUTH_JWT_ISSUER` | `https://auth.panakoes.com` | `iss` claim |
| `AUTH_JWT_AUDIENCE` | `panakoes-api` | `aud` claim |
| `AUTH_JWT_EXPIRES_IN_SECONDS` | `3600` | JWT lifetime |
| `AUTH_JWT_ALGORITHM` | `HS256` | Signing algorithm; `HS256` (default; shared-secret path) or `RS256` (KMS-backed; see below) |
| `AUTH_JWT_KMS_KEY_ID` | (required when RS256) | KMS key id, alias, or ARN. Ignored when `AUTH_JWT_ALGORITHM=HS256` |
| `AWS_REGION` | `us-east-1` | Region for the KMS client (used only when `AUTH_JWT_ALGORITHM=RS256`) |
| `BETTER_AUTH_URL` | (see `.env.example`) | Better-Auth base URL |

### RS256 + JWKS (ADR-041 phase 1, opt-in)

Set `AUTH_JWT_ALGORITHM=RS256` and `AUTH_JWT_KMS_KEY_ID=alias/panakoes-dev-jwt-signing` (or any KMS key id / alias / ARN that points at an `RSA_2048` asymmetric CMK with `key_usage=SIGN_VERIFY`). The service signs JWTs via `kms:Sign`; the private key material lives entirely inside KMS and the service never sees it. The JWKS endpoint at `GET /.well-known/jwks.json` returns the public key (fetched once via `kms:GetPublicKey`, cached for 10 minutes in-process). The JWT header carries a `kid` matching the KMS key's UUID so verifiers can match the JWKS entry directly.

When `AUTH_JWT_ALGORITHM=HS256` (default), the JWKS endpoint returns `{"keys": []}` so consumers can wire their JWKS-fetch logic against a stable URL ahead of the phase-2 cutover.

Migration plan: see [ADR-041](../../docs/adr/ADR-041-rs256-via-aws-kms-with-jwks.md).

In production these come from AWS Secrets Manager / SSM Parameter Store, never from a committed file. To generate a fresh signing secret:

```bash
node -e "console.log(require('crypto').randomBytes(48).toString('base64url'))"
```

- TypeScript (ESM-only, Node 22+)
- Hono 4 web framework + `@hono/node-server`
- Better-Auth (email/password provider, database-backed sessions)
- Drizzle ORM + `postgres-js` driver
- jose (HS256 JWT signing/verification, including step-up tokens)
- otpauth (RFC 6238 TOTP for step-up MFA)
- Pino structured logging
- Zod for env-var and request-body validation

## Local setup
## Local development

```bash
pnpm install
cp .env.example .env   # then fill in AUTH_JWT_SECRET (>= 32 bytes) + DATABASE_URL
pnpm db:migrate        # applies drizzle/0000_initial.sql against $DATABASE_URL
pnpm dev               # starts the service on PORT (default 8080)
```

To generate a fresh secret:

```bash
node -e "console.log(require('crypto').randomBytes(48).toString('base64url'))"
```

## Environment variables

See `.env.example` for the full list. Required:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string |
| `AUTH_JWT_SECRET` | HS256 signing secret; must be at least 32 bytes |

Optional (with defaults documented in `.env.example`): `PORT`, `LOG_LEVEL`, `NODE_ENV`, `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE`, `AUTH_JWT_EXPIRES_IN_SECONDS`, `BETTER_AUTH_URL`.

In production these come from AWS Secrets Manager / SSM Parameter Store, never from a committed file.

## Database schema

Better-Auth manages four tables: `user`, `session`, `account`, `verification`. The `account` table holds the password hash for the email+password provider (Argon2id by Better-Auth default). See [`src/db/schema.ts`](src/db/schema.ts) and the initial migration at [`drizzle/0000_initial.sql`](drizzle/0000_initial.sql).

The v0.1 spec described a slimmer two-table layout (`users` + `sessions`); this implementation expands it to the full Better-Auth shape because Better-Auth requires all four tables to function. The public API still matches the spec exactly.

### RBAC role column

[`drizzle/0001_add_role.sql`](drizzle/0001_add_role.sql) adds `user.role text NOT NULL DEFAULT 'user'` with a CHECK constraint restricting values to `user` or `admin`. The role is read on every sign-in/sign-up and embedded into the JWT as a `role` claim so downstream services can authz without a per-request DB hit. CHECK was chosen over a Postgres ENUM type so future role additions are non-destructive `ALTER TABLE` statements.

Slice-1 admin assignment is a manual SQL update; a real role-management API lands in slice 2.

### Step-up MFA (slice-1 stub)

[`src/auth/mfa.ts`](src/auth/mfa.ts) implements RFC 6238 TOTP enrolment + verification via the `otpauth` library. The slice-1 routes are wire-shape stubs: they exercise the enrol -> verify -> step-up-token flow end-to-end but do NOT persist secrets server-side. The `verify` endpoint accepts the `secret_key` in the request body so the round-trip is testable; slice 2 will store an encrypted secret on the user row and remove that body field.

Step-up tokens carry `step_up: true`, a 5-minute exp, and the same `sub`/`email`/`role` as the access token. Tier 3 admin routes call `POST /mfa/challenge` to assert a step-up token is present; the gate returns `401 WWW-Authenticate: StepUp` if not.

## Testing

```bash
pnpm test              # full vitest run with coverage
pnpm test:watch        # watch mode for TDD
pnpm biome check       # lint + format check
pnpm tsc --noEmit      # type-check
```

Integration tests use [testcontainers-node](https://node.testcontainers.org/) to spin up a real Postgres 16 container per test session. The first run pulls the image; subsequent runs reuse the cached layers. No mocking of the database, per ADR-018.

## Deployment

**Canonical bake path is GitHub Actions** (`.github/workflows/image-bake-on-change.yml` on push to `main`, or the manual `image-bake-manual.yml` workflow from the Actions UI). The command below is a fallback for offline local development only.

```bash
docker build -t panakoes-auth .
```

The Dockerfile is multi-stage: a builder stage installs dependencies and compiles TypeScript with `tsc`, then prunes dev deps; the runtime stage copies the resolved `node_modules` plus `dist/` into a minimal `node:22-slim` image and runs as a non-root `app` user on port 8080. The image is published to ECR by the GHA bake workflow and deployed via Terraform-managed ECS / Fargate (TODO: wire the Terraform module once infra slice lands).

## Architecture notes

- JWT payload: `{sub: user_uuid, email, role, iat, exp, jti: session_uuid}`. The `jti` is the session UUID; other services hit `/validate` to check session-revocation freshness when they need real-time accuracy. Without that hit, JWT verification alone gives up-to-1-hour staleness window before the token expires.
- Step-up token payload: `{sub, email, role, step_up: true, iat, exp}` with a 5-minute exp. No `jti`; step-up tokens are not session-bound, they sit alongside the regular access token to gate Tier 3 calls.
- Better-Auth's rate-limiter is enabled (30 req/60s). Its own handler at `/api/auth/*` is the throttled surface.
- Service refuses to boot if `AUTH_JWT_SECRET` is missing or shorter than 32 bytes (zod gate at startup).
- `GET /.well-known/jwks.json` returns `{"keys": []}` today (HS256 has no public key). Slice 2 (per ADR-022) flips this endpoint to surface RS256 public keys with `kid` rotation.
- **Tech stack:** TypeScript (ESM-only, Node 22+), Hono 4 + `@hono/node-server`, Better-Auth (email/password provider, database-backed sessions), Drizzle ORM + `postgres-js`, jose (HS256), Pino, Zod for env-var and request-body validation.
- **JWT payload:** `{sub: user_uuid, email, iat, exp, jti: session_uuid}`. The `jti` is the session UUID; other services hit `/validate` to check session-revocation freshness when they need real-time accuracy. Without that hit, JWT verification alone gives up-to-1-hour staleness window before the token expires.
- **Better-Auth tables:** four tables (`user`, `session`, `account`, `verification`); the `account` table holds the password hash for the email+password provider (Argon2id by Better-Auth default). See [`src/db/schema.ts`](src/db/schema.ts) and the initial migration at [`drizzle/0000_initial.sql`](drizzle/0000_initial.sql). The v0.1 spec described a slimmer two-table layout (`users` + `sessions`); this implementation expands it to the full Better-Auth shape because Better-Auth requires all four tables to function. The public API still matches the spec exactly.
- **Rate limiting:** Better-Auth's built-in rate-limiter is enabled (30 req/60s) on the `/api/auth/*` surface.
- **Boot-time validation:** service refuses to boot if `AUTH_JWT_SECRET` is missing or shorter than 32 bytes (zod gate at startup).
- **Slice 2 follow-up:** migrate to RS256 + JWKS before any second service starts consuming JWTs in non-trusted contexts (per ADR-005).
- **Coverage gate:** 100% on auth-related code per ADR-018; CI fails the PR below threshold.

## Database migrations

Two ways to apply migrations exist, with different audiences.

**Local dev (developer machine):** use the drizzle-kit dev workflow.

```bash
DATABASE_URL=postgres://... pnpm db:migrate
```

`drizzle-kit` is a devDependency and stays available locally.

**Dev / prod Aurora (operator):** use the runtime runner that ships in the production container image. `drizzle-kit` is pruned out of the runtime image by `pnpm prune --prod`, so the production path uses `dist/migrate.js` (compiled from [`src/migrate.ts`](src/migrate.ts)).

Local build + run against any `DATABASE_URL`:

```bash
pnpm build
DATABASE_URL=postgres://... pnpm db:migrate:runtime
```

Against the dev Aurora cluster via a one-off ECS task that reuses the existing auth task definition:

```bash
AWS_PROFILE=panakoes-admin ./scripts/run-auth-migration.sh
# defaults: AWS_REGION=us-east-1, CLUSTER=panakoes-dev, SERVICE=panakoes-dev-auth
```

The wrapper script discovers the task definition, subnets, and security groups from the running auth service, launches one Fargate task with `command` overridden to `["node","dist/migrate.js"]`, polls until the task stops, prints the container's CloudWatch logs, and exits with the container's exit code so CI / operators see green / red directly.

### Why `__migrations` + sha256

The runner maintains a `__migrations(filename text primary key, hash text, applied_at timestamptz)` table. On each invocation it lists `drizzle/*.sql` sorted lexicographically and for each file:

1. If the filename is recorded with a matching sha256, skip it.
2. If the filename is recorded but the sha256 differs, fail loudly (someone edited an applied file; that is almost always a mistake and needs a human eye).
3. Otherwise apply the file inside a transaction, write the `__migrations` row in the same transaction, and continue.

Each file is one transaction, so a failing statement rolls back both the partial schema change and the bookkeeping row. The runner does NOT run automatically on auth service startup; auto-applying on boot would race with rolling deploys and couple schema changes to image promotion. Operator-invoked is deliberate. Logs are single-line JSON (`{"level":"info","msg":"migration_applied","file":"0000_initial.sql"}`) so CloudWatch Logs Insights queries are trivial.

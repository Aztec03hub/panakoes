# services/auth

The Panakoes Auth microservice. Issues short-lived JWTs (HS256, 1-hour expiry) backed by database-managed sessions, and exposes a `/auth/validate` endpoint other services call to confirm a token's session has not been revoked.

This is the v0.1 MVP. See [`PLANNING.md`](../../PLANNING.md) ADR-005 for the long-form decision record. Slice 2 will migrate to RS256 + JWKS before any second service starts consuming JWTs in non-trusted contexts.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness; returns `{"status":"ok","service":"auth"}` |
| POST | `/auth/sign-up` | Create user + session, returns JWT |
| POST | `/auth/sign-in` | Authenticate user, returns JWT |
| POST | `/auth/sign-out` | Revoke the session bound to the bearer token |
| POST | `/auth/validate` | Verify JWT + session freshness; returns `{valid, user}` |

Better-Auth's own handler is also mounted at `/api/auth/*` for direct use of its built-in flows (e.g. `GET /api/auth/get-session`).

## Tech stack

- TypeScript (ESM-only, Node 22+)
- Hono 4 web framework + `@hono/node-server`
- Better-Auth (email/password provider, database-backed sessions)
- Drizzle ORM + `postgres-js` driver
- jose (HS256 JWT signing/verification)
- Pino structured logging
- Zod for env-var and request-body validation

## Local setup

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

## Testing

```bash
pnpm test              # full vitest run with coverage
pnpm test:watch        # watch mode for TDD
pnpm test:coverage     # explicit coverage report
```

Integration tests use [testcontainers-node](https://node.testcontainers.org/) to spin up a real Postgres 16 container per test session. The first run pulls the image (a few seconds); subsequent runs reuse the cached layers. No mocking of the database, per ADR-018.

Coverage gate: 100% on auth-related code (per ADR-018). The vitest config enforces this and CI fails the PR below threshold.

## Linting and type-checking

```bash
pnpm biome check       # lint + format check (replaces eslint + prettier)
pnpm tsc --noEmit      # type-check
```

## Building the Docker image

```bash
docker build -t panakoes-auth .
```

The Dockerfile is multi-stage: a builder stage installs dependencies and compiles TypeScript with `tsc`, then prunes dev deps; the runtime stage copies the resolved `node_modules` plus `dist/` into a minimal `node:22-slim` image and runs as a non-root `app` user on port 8080.

## Architecture notes

- JWT payload: `{sub: user_uuid, email, iat, exp, jti: session_uuid}`. The `jti` is the session UUID; other services hit `/auth/validate` to check session-revocation freshness when they need real-time accuracy. Without that hit, JWT verification alone gives up-to-1-hour staleness window before the token expires.
- Better-Auth's rate-limiter is enabled (30 req/60s). Its own handler at `/api/auth/*` is the throttled surface.
- Service refuses to boot if `AUTH_JWT_SECRET` is missing or shorter than 32 bytes (zod gate at startup).

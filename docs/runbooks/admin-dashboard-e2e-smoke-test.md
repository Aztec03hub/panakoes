# Admin dashboard end-to-end smoke test

## Purpose

Prove that all four runtime layers of the Panakoes admin dashboard are healthy
and wired together correctly:

1. **SPA build + CDN.** The SvelteKit bundle is in S3, served via CloudFront,
   and contains the build-time `VITE_API_BASE_URL` pointing at the live API
   Gateway. (`services/admin/src/lib/config.ts`.)
2. **API Gateway.** The HTTP API v2 in front of every backend service applies
   the (c+) routing shape from [ADR-038](../adr/038-api-gateway-routing-strategy.md):
   `ANY /v1/<service>/{proxy+}` per service, with explicit overrides for
   sign-up / sign-in.
3. **Backend services.** auth, cost-api, and admin-api each respond on their
   canonical internal paths after the gateway strips the `/v1/<service>/`
   prefix.
4. **State.** Aurora (auth) under the split-credential model from
   [ADR-039](../adr/ADR-039-auth-db-application-role-and-migration-runner.md)
   accepts a sign-in. DynamoDB (cost-api) and the admin-api lifecycle log
   either return data or a coherent empty state, never a 5xx.

This runbook is the lightweight counterpart to
[`auth-db-first-deploy.md`](auth-db-first-deploy.md): that one stands the
auth-db cluster up from zero, this one validates the full integrated path
every time a service redeploys.

## When to use this runbook

Run after any of the following changes have landed in dev:

- A service deploy of `auth`, `cost-api`, `admin-api`, or any future
  dashboard-backing service.
- A `terraform apply` of `infra/dev/api-gateway/`, `infra/dev/ecs/`,
  `infra/dev/networking/`, `infra/dev/iam/`, or `infra/dev/secrets/`.
- A new admin SPA build pushed to S3 / CloudFront (the bake-time
  `VITE_API_BASE_URL` is invisible from infra; a working smoke is the only
  signal it was injected correctly).
- After any incident close-out per `incident-response.md`, before declaring
  the dev environment "green".

For pure schema / migration validation against a freshly-applied auth-db,
prefer `auth-db-first-deploy.md` step 9 instead; this runbook assumes that
work is already done.

## Dev environment reference

| Field | Value |
|---|---|
| Admin SPA | `https://dmaopcm3hnxog.cloudfront.net/` |
| API Gateway base | `https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev` |
| Auth routes | `POST /v1/auth/sign-up`, `POST /v1/auth/sign-in`, `GET /v1/auth/health` |
| Cost-api routes | `GET /v1/cost-api/api/v1/cost/by-service`, `/by-tenant`, `/forecast`, `/anomalies` |
| Admin-api routes | `GET /v1/admin-api/api/v1/admin/lifecycle/*`, `/audit-log` |
| First admin user | `phil@lafayettelabs.com` (role=admin); password held locally at `/tmp/admin_pw.txt`, never in the repo |
| AWS profile | `panakoes-admin` (account `659225405128`, region `us-east-1`) |
| ECS cluster | `panakoes-dev` |
| Aurora cluster (auth) | `panakoes-dev-auth-20260510055543895900000001` |

Export the AWS context once at shell start:

```bash
export AWS_PROFILE=panakoes-admin
export AWS_REGION=us-east-1
export API=https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev
export SPA=https://dmaopcm3hnxog.cloudfront.net
```

## Prerequisites

| Tool | Verification |
|---|---|
| `aws` CLI v2 | `aws --version` |
| `curl` 7.x+ | `curl --version` |
| `jq` | `jq --version` |
| `node` 22.x (for the optional Playwright step) | `node --version` |
| `pnpm` 11.0.8 (only for the Playwright step) | `pnpm --version` |
| Working internet + access to `*.execute-api.us-east-1.amazonaws.com` and `*.cloudfront.net` | a 200 on `GET $API/v1/auth/health` |

## Pre-flight checklist

Run all of the following before starting the layer-by-layer test. Why: a
failure inside the procedure can have many causes; verifying the platform
state up front isolates the test signal from steady-state infra drift.

```bash
# 1. ECS services running 1/1 each. Expect three rows with runningCount == desiredCount.
aws ecs describe-services \
  --cluster panakoes-dev \
  --services panakoes-dev-auth panakoes-dev-cost-api panakoes-dev-admin-api \
  --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount,deployments:length(deployments)}' \
  --output table

# 2. Target group health on each NLB target group: every target Healthy.
for svc in auth cost-api admin-api; do
  TG=$(aws elbv2 describe-target-groups \
        --names panakoes-dev-${svc} \
        --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null) \
    && aws elbv2 describe-target-health --target-group-arn "$TG" \
        --query 'TargetHealthDescriptions[].{target:Target.Id,state:TargetHealth.State}' \
        --output table
done

# 3. ECR images present. Expect a recent imagePushedAt for each repo.
for repo in panakoes-dev-auth panakoes-dev-cost-api panakoes-dev-admin-api; do
  aws ecr describe-images --repository-name "$repo" \
    --query 'sort_by(imageDetails,&imagePushedAt)[-1].{tag:imageTags[0],pushed:imagePushedAt}' \
    --output table
done

# 4. Runtime secrets populated, NOT placeholder. We check CreatedDate as a
#    proxy; the smoke test below catches an actual placeholder value via the
#    503/500 failure mode. NEVER print the secret value.
for s in panakoes-dev/database-url panakoes-dev/jwt-signing-secret; do
  aws secretsmanager describe-secret --secret-id "$s" \
    --query '{name:Name,lastChanged:LastChangedDate}' --output table
done
```

If any check fails, do not proceed; route to the matching runbook
(`incident-response.md` for service-down, `auth-db-first-deploy.md` for
fresh-cluster issues, `dev-troubleshooting.md` for local-tooling friction).

## Procedure

### Layer A: backend direct (curl)

Why: hitting the gateway directly with curl removes the SPA and the browser
from the chain, so any failure here is unambiguously API Gateway, a service,
or its data store. If layer A is clean, every subsequent layer-B / layer-C
failure is a frontend wiring problem, not a backend problem.

#### A.1 Auth health

Why: cheapest possible end-to-end check. Validates DNS, TLS, API Gateway
routing, VPC link, NLB, target group health, and that the auth container
booted. No DB access required; the auth `/health` handler is in-process.

```bash
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' \
  "$API/v1/auth/health"
```

Expected: `200 ~0.10s` (warm), `200 ~0.30s` (cold first hit after Aurora
scale-from-zero or NLB target re-register).

#### A.2 Sign-up

Why: exercises auth -> Aurora write path under the `auth_app` least-privileged
role (per ADR-039). A 500 with `permission denied for table "user"` here
implicates the grant set from `auth-db-first-deploy.md` step 6; a 500 with
`JwtConfigError` implicates the signing-secret env wiring; a 503 implicates
the gateway integration.

```bash
SMOKE_EMAIL="smoke-$(date -u +%Y%m%dT%H%M%SZ)@test.test"
SMOKE_PW='S3cure!Smoke#2026'

curl -sS -X POST "$API/v1/auth/sign-up" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$SMOKE_PW\",\"name\":\"Smoke\"}" \
  | tee /tmp/smoke-signup.json | jq '{user:.user.email, role:.user.role, hasToken: (.token != null)}'
```

Expected: HTTP 201, JSON body with `token` and `user.email == $SMOKE_EMAIL`,
`user.role == "user"`. The smoke user persists in the DB; clean up at the
end (see "Cleanup").

#### A.3 Sign-in

Why: validates the password hash check + JWT issuance independently from
the sign-up path. Cheap; also gives us a fresh token to use in A.4.

```bash
TOKEN=$(curl -sS -X POST "$API/v1/auth/sign-in" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$SMOKE_EMAIL\",\"password\":\"$SMOKE_PW\"}" \
  | tee /tmp/smoke-signin.json | jq -r '.token')

[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] && echo "got token (len=${#TOKEN})"
```

Expected: HTTP 200, `token` populated.

#### A.4 Cost-api with JWT (KNOWN-FAILING)

Why: this is the multi-layer test that catches JWT issuer / audience
mismatches between auth and cost-api (the bug PR #218 fixed in
`infra/dev/ecs/`).

```bash
curl -sS -o /tmp/smoke-cost.body -w '%{http_code}\n' \
  -H "authorization: Bearer $TOKEN" \
  "$API/v1/cost-api/api/v1/cost/by-service?from=2026-05-01&to=2026-05-11"
```

**Expected today (2026-05-11):** HTTP 503. This is the multi-query-param
gateway bug captured in memory entry
`aws_api_gateway_503_multi_query_params.md`; the issue is in the API Gateway
integration's parameter mapping when more than one query-string parameter
is present. Until that PR lands, **a 503 here is a PASS for this smoke
step**. Surface it explicitly in the run notes; do not treat it as a fresh
incident.

**Expected after the multi-query-param fix lands:** HTTP 200 with a JSON
body (possibly an empty `items` array if cost DDB is not yet seeded). HTTP
401 here means the JWT issuer / audience drift regressed; cross-check
`JWT_ISSUER` / `JWT_AUDIENCE` on the cost-api task def against
`AUTH_JWT_ISSUER` / `AUTH_JWT_AUDIENCE` on the auth task def.

#### A.5 Cost-api single-param fallback

Why: keeps a working positive signal in the smoke until A.4's 503 clears.
Single query-param requests are unaffected by the multi-param bug.

```bash
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "authorization: Bearer $TOKEN" \
  "$API/v1/cost-api/api/v1/cost/by-service?from=2026-05-01"
```

Expected: HTTP 200 (data or empty state) or HTTP 422 (cost-api rejected the
missing `to`). Either is acceptable; what we want NOT to see is 5xx or 401.

### Layer B: SPA static (unauthenticated)

Why: validates the CloudFront + S3 + bundle build end of the chain. The
`/dashboard` route ships with bundled static JSON mocks under
`services/admin/static/dashboard/` when `VITE_USE_LIVE_HEALTH_AGGREGATOR=false`
(today's default), so it should render Tier-1 health cards even without
auth or backend.

Steps in the browser:

1. Open `https://dmaopcm3hnxog.cloudfront.net/` in a fresh incognito window.
2. Navigate to `/dashboard`. Expect the health-overview cards to render
   (counts, status pills); not a blank page, not a "Loading health
   snapshot..." spinner that never resolves.
3. Open devtools Network panel. Confirm:
   - `index.html` returns 200 from CloudFront (check `x-cache: Hit/Miss`).
   - The hashed JS chunk for the dashboard route returns 200.
   - `dashboard/health.json` (or equivalent) loads from the same CloudFront
     origin (NOT from the API Gateway). This proves the static-mock
     fallback is in play.

A stuck loading spinner here implicates the Svelte 5 runes migration
(CHANGELOG entry under Fixed: services/admin onMount migration) regressed;
re-check the dashboard chunk contains a real `await fetchHealth()` callback.

### Layer C: SPA authenticated

Why: this is the user-visible smoke. Validates auth wiring inside the SPA,
the build-time `VITE_API_BASE_URL`, the (c+) URL composition per ADR-038,
and the cross-origin CORS allow-list on the gateway.

Steps in the browser (same incognito window):

1. Navigate to `/login`.
2. Fill in:
   - Email: `phil@lafayettelabs.com`.
   - Password: contents of `/tmp/admin_pw.txt` (Phil's local file; never
     paste, copy from the file).
3. Submit. Expect a redirect to `/dashboard`. The Network panel should show
   `POST https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev/v1/auth/sign-in`
   returning 200.
4. Click `/cost` (or any cost-* sub-route).
5. Expect either real data or a coherent empty state ("No services found
   for this window"), but **not** an inline alert that reads "Session
   expired" or a redirect back to `/login`.

If `/cost` returns 503 from A.4's bug, the SPA today shows an alert about
the cost-api being unavailable. That alert is also acceptable while the
multi-query-param bug is open; what is NOT acceptable is the SPA showing a
spinner that never resolves (means the SPA is not handling the 503 at all,
implicating PR #221's "Session expired" + error-surface wiring).

## Playwright script

Why: mechanizes layers B and C for repeatable post-deploy verification.
Drop in `services/admin/tests/e2e/smoke.spec.ts` when the e2e harness is
fully wired; for now this is a copy-paste reference. Reads the admin
password from the env var `ADMIN_PW` so the secret never appears in the
repo or in a shell history file.

```ts
import { test, expect } from "@playwright/test";

const SPA = "https://dmaopcm3hnxog.cloudfront.net";
const API = "https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev";
const ADMIN_EMAIL = "phil@lafayettelabs.com";

test("admin dashboard end-to-end smoke", async ({ page }) => {
  const adminPw = process.env.ADMIN_PW;
  if (!adminPw) throw new Error("ADMIN_PW env var required; do not hardcode");

  // Layer B: unauthenticated dashboard renders from static mocks
  await page.goto(`${SPA}/dashboard`);
  await expect(page.getByRole("heading", { name: /health/i })).toBeVisible({
    timeout: 10_000,
  });

  // Layer C: login + redirect
  await page.goto(`${SPA}/login`);
  await page.getByLabel(/email/i).fill(ADMIN_EMAIL);
  await page.getByLabel(/password/i).fill(adminPw);
  const signInRequest = page.waitForResponse(
    (r) => r.url() === `${API}/v1/auth/sign-in` && r.status() === 200,
  );
  await page.getByRole("button", { name: /sign in/i }).click();
  await signInRequest;
  await expect(page).toHaveURL(new RegExp(`${SPA}/dashboard`));

  // Layer C: cost route renders without "Session expired"
  await page.goto(`${SPA}/cost/by-service`);
  await expect(page.getByText(/session expired/i)).toHaveCount(0);
});
```

Curl-equivalent that mechanizes layers A.1-A.3 (useable in CI today,
without a browser):

```bash
#!/usr/bin/env bash
set -euo pipefail

API=${API:-https://n2un8ica69.execute-api.us-east-1.amazonaws.com/dev}
EMAIL="smoke-$(date -u +%Y%m%dT%H%M%SZ)@test.test"
PW='S3cure!Smoke#2026'

code=$(curl -sS -o /dev/null -w '%{http_code}' "$API/v1/auth/health")
[ "$code" = "200" ] || { echo "health failed: $code"; exit 1; }

code=$(curl -sS -o /tmp/su.json -w '%{http_code}' -X POST "$API/v1/auth/sign-up" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\",\"name\":\"Smoke\"}")
[ "$code" = "201" ] || { echo "sign-up failed: $code"; cat /tmp/su.json; exit 1; }

TOKEN=$(curl -sS -X POST "$API/v1/auth/sign-in" \
  -H 'content-type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" | jq -r '.token')
[ -n "$TOKEN" ] && [ "$TOKEN" != "null" ] || { echo "sign-in returned no token"; exit 1; }

echo "smoke_ok email=$EMAIL"
```

## Expected timings

Baseline against a warm dev environment; anything 5x slower is suspect and
worth investigation (likely Aurora cold-start, NLB target re-register, or a
service container OOM-and-restart loop).

| Step | Cold | Warm | 5x-slower trigger |
|---|---|---|---|
| `GET /v1/auth/health` | 300 ms | 100 ms | NLB target draining / gateway throttling |
| `POST /v1/auth/sign-up` | 800 ms | 200 ms | Aurora scale-from-zero or grant misconfig |
| `POST /v1/auth/sign-in` | 400 ms | 150 ms | bcrypt cost setting too high |
| `GET /v1/cost-api/.../by-service` (1 param) | 500 ms | 50 ms | DDB throttling or cache miss storm |
| SPA `/dashboard` first paint | 2.0 s | 0.5 s | CloudFront cache miss + cold S3 |
| Playwright full run (B + C) | 12 s | 6 s | any of the above stacked |

## Cleanup

Why: the smoke test leaves one user row in `"user"` per run, plus a session
row and an account row. They accumulate forever otherwise; the cleanup also
exercises the master-credential path one more time, which is a small bonus
validation that ADR-039's posture still works.

Run a one-off ECS task against the migrate task-def (same shape as
`auth-db-first-deploy.md` step 6's grant invocation) with this Node
override:

```bash
CLEANUP_JS=$(cat <<'JS'
import postgres from "postgres";
const sql = postgres(process.env.DATABASE_URL, {max:1, prepare:false});
const rows = await sql`DELETE FROM "user" WHERE email LIKE 'smoke-%@test.test' RETURNING email`;
console.log(JSON.stringify({level:"info",msg:"smoke_users_deleted",count:rows.count,rows}));
await sql.end();
JS
)
# run-task using $MIGRATE_TD_ARN per auth-db-first-deploy.md step 4-5.
```

Expected output: a count > 0 with the deleted emails. Cascade FKs from
`session` and `account` to `user` clear the matching rows automatically.

The single user from `smoke@test.test` (no timestamp suffix) that was
captured in the runbook's example commands also matches the
`'smoke-%@test.test'` prefix and will be cleaned by this same query
because the prefix wildcard matches `smoke-` + anything (NOT `smoke@`
without the dash). If you used the no-dash form during a manual run,
adjust the `LIKE` pattern to `'smoke%@test.test'`.

## Failure-mode map

When a step fails, match the response code + log signature against this
table to localize the failure to one layer before reaching for deeper
diagnostics.

| Symptom | Likely layer | First thing to check |
|---|---|---|
| 502 from gateway on any backend route | Integration / NLB / target health | `aws elbv2 describe-target-health --target-group-arn <tg>`; expect every target `Healthy`. Unhealthy = container failing health probes or wrong-port wiring. |
| 503 from gateway on **single** route | Integration | API Gateway integration mis-targeted (wrong NLB listener ARN). Check `aws apigatewayv2 get-integrations --api-id <id>`. |
| 503 from gateway on `?from=...&to=...` only | Known gateway bug | Multi-query-param parameter-mapping bug, see memory `aws_api_gateway_503_multi_query_params.md`. PASS as known-failing on this smoke; track in the fix PR. |
| 401 from cost-api with valid token | Backend service (env wiring) | JWT issuer / audience mismatch. Cross-check cost-api task def `JWT_ISSUER` + `JWT_AUDIENCE` against auth task def `AUTH_JWT_ISSUER` + `AUTH_JWT_AUDIENCE` (the PR #218 fix). |
| 500 from any service with `JwtConfigError` in logs | Backend service (env wiring) | `DATABASE_URL` placeholder still in Secrets Manager, OR the IAM secret-read policy missing the secret ARN, OR `JWT_SECRET` env-var-name mismatch (PR #218 territory). |
| 500 from auth with `permission denied for table "user"` | DB grants | The `auth_app` role is missing one or more of SELECT/INSERT/UPDATE/DELETE on the Better-Auth tables. Re-run `auth-db-first-deploy.md` step 6 + verification. |
| Dashboard renders but every cost page shows empty | Data (DDB seed) | `cost-api` DDB tables not seeded. Run `scripts/seed-cost-api-dynamodb.py` (in-flight PR). The path itself is healthy; the data store is empty. |
| Dashboard shows "Session expired" inline alert without redirect | SPA auth wiring | PR #221 in flight. SPA is catching the 401 but not routing back to `/login`. Confirm the unwired-401 behavior by checking the Network panel for an unhandled 401. |
| `/dashboard` spins on "Loading health snapshot..." forever | SPA build / lifecycle | The Svelte 5 runes migration (CHANGELOG Fixed entry) regressed; the lifecycle init compiled empty. Check the dashboard chunk in devtools Sources for the real `await fetchHealth()` callback. |
| Browser console: CORS preflight rejection | Gateway CORS | The SPA origin is not in `var.cors_allow_origins` on `infra/dev/api-gateway/`. Re-apply with the dev CloudFront origin added (see CHANGELOG Changed entry). |
| Sign-up succeeds but sign-in 401s with correct password | Auth password verification | Almost always indicates a partial migration or stale image; force-redeploy the auth service and retry. |

## What this validates for interviews

Three patterns worth surfacing in a senior-architecture interview:

- **Multi-layer integration testing, not just unit tests.** Service-level
  tests pass independently in CI; the smoke proves they compose end-to-end
  against real AWS surface (API Gateway HTTP API v2 quirks, NLB target
  health, IAM secret-read policies, Secrets Manager rotation timing,
  CloudFront cache state). Unit-test green is necessary but not sufficient;
  the integration smoke is the boundary check.
- **Infra/app coupling made explicit.** The runbook reads top-to-bottom
  through every layer that has to be configured correctly: bake-time env
  vars in the SPA (`VITE_API_BASE_URL`), routing decisions in API Gateway
  (the (c+) shape, per-service proxy), env-var wiring in ECS task defs
  (`JWT_ISSUER` matching across services), Secrets Manager values, IAM
  grants. The failure-mode map names the layer for each symptom so the
  on-call engineer routes the work in seconds, not minutes.
- **Ops discipline gate before declaring "green".** A passing smoke is the
  release-gate signal. The runbook is short enough to run after every
  deploy (under five minutes for layers A + B + C), and the curl variant
  is CI-friendly so it can become an automated post-deploy check once the
  multi-query-param 503 bug clears. That progression (manual smoke -> CI
  smoke -> blocking deploy gate) is the standard maturation of a
  post-deploy validation pipeline.

## References

- [ADR-038: API Gateway routing strategy (c+ shape)](../adr/038-api-gateway-routing-strategy.md)
- [ADR-039: Auth DB split-credential model and operator-invoked migration runner](../adr/ADR-039-auth-db-application-role-and-migration-runner.md)
- [`auth-db-first-deploy.md`](auth-db-first-deploy.md) for the one-time
  cluster bring-up procedure this runbook complements.
- [`incident-response.md`](incident-response.md) for what to do when this
  smoke fails outside a deploy window.
- [`dev-troubleshooting.md`](dev-troubleshooting.md) for local-tooling
  friction encountered while running the curl / Playwright steps.
- `services/admin/src/lib/config.ts` for the env-driven URL base map.
- `services/admin/README.md` for the `VITE_API_BASE_URL` contract and
  per-service URL composition table.
- Memory: `aws_api_gateway_503_multi_query_params.md` (the known-failing
  A.4 step).

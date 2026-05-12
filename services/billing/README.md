# services/billing

Billing microservice for Panakoes. Handles Stripe-backed subscription
lifecycle for the Pro and Team tiers and persists every billing event
in DynamoDB. Mirrors the `services/_template/` and
`services/ingestion-api/` patterns.

## Stripe TEST mode only (this slice)

This v0.1 slice runs against Stripe in TEST mode only. **No live key
path exists in this slice.** The `Settings` validator rejects any
`STRIPE_API_KEY` that does not start with `sk_test_`, so the service
literally cannot boot with a live key. The webhook signature secret
(`STRIPE_WEBHOOK_SECRET`) is similarly a TEST-mode secret here.

Real billing wiring (live keys, production webhook endpoint, customer
portal branding, real product catalog) lands in a follow-up slice.
This slice exists to lock down the shape, contracts, and security
paths.

## Endpoints

| Method | Path                       | Auth   | Description |
| ---    | ---                        | ---    | --- |
| GET    | `/health`                  | no     | Liveness probe |
| POST   | `/checkout-session`        | Bearer | Create a Stripe Checkout Session for `pro` or `team` |
| POST   | `/portal-session`          | Bearer | Create a Stripe Customer Portal session for the JWT subject (any plan, including free) |
| POST   | `/webhook`                 | Stripe-Signature | Handle Stripe webhooks (signature-verified) |
| GET    | `/subscription`            | Bearer | Return the JWT subject's current subscription view |

The webhook endpoint is intentionally unauthenticated at the JWT
layer: Stripe does not send a Panakoes JWT. Authentication is the
`Stripe-Signature` header verified against `STRIPE_WEBHOOK_SECRET`.

### Customer Portal session

`POST /portal-session` works for every authenticated user,
including free-tier accounts (so they can upgrade from inside the
hosted portal). Request body:

```json
{ "return_url": "https://panakoes.com/account" }
```

`return_url` is validated against a Panakoes-owned allowlist (see
`_ALLOWED_RETURN_URL_ORIGINS` in `routes/billing.py`):

- `https://dmaopcm3hnxog.cloudfront.net/*`
- `https://panakoes.com/*`

Any other origin (including a `http://` downgrade or a subdomain
confusion like `panakoes.com.evil.com`) is rejected with `422`. We
never accept a user-controlled redirect target without validation;
the portal cookie + Stripe's own redirect would otherwise be a phishing
vector. When the JWT subject has no Stripe customer on file yet, the
route creates one via `stripe.Customer.create(email=..., metadata={user_id})`
and persists a `customer_created` event so subsequent portal sessions
reuse the same customer id. Response shape:

```json
{ "url": "https://billing.stripe.com/session/..." }
```

The SPA then `window.location.assign(response.url)` to send the user
into the hosted portal. The portal triggers the same
`customer.subscription.*` and `invoice.*` webhooks the checkout flow
already handles, so plan changes, card updates, and cancellations
flow through the existing webhook handler without new code.

## Configuration

Read from environment variables (see
`src/panakoes_billing/config.py`):

| Variable | Default | Notes |
| --- | --- | --- |
| `JWT_SECRET` | (dev placeholder) | Must match the Auth service's HS256 secret |
| `JWT_ISSUER` | `https://auth.panakoes.com` | Claim-validated |
| `JWT_AUDIENCE` | `panakoes-api` | Claim-validated |
| `STRIPE_API_KEY` | (placeholder, must start with `sk_test_`) | Live keys rejected at startup |
| `STRIPE_WEBHOOK_SECRET` | (placeholder) | Stripe webhook signing secret |
| `STRIPE_PRICE_PRO` | (placeholder) | Stripe Price ID for the Pro tier |
| `STRIPE_PRICE_TEAM` | (placeholder) | Stripe Price ID for the Team tier |
| `STRIPE_SUCCESS_URL` | `http://localhost:3000/billing/success` | Checkout success redirect |
| `STRIPE_CANCEL_URL` | `http://localhost:3000/billing/cancel` | Checkout cancel redirect |
| `STRIPE_PORTAL_RETURN_URL` | `http://localhost:3000/account` | Portal return redirect |
| `DDB_BILLING_TABLE` | `panakoes-dev-billing-events` | Provisioned by Terraform |
| `DDB_SUBSCRIPTIONS_TABLE` | `panakoes-dev-subscriptions` | Provisioned by Terraform |
| `AWS_REGION` | `us-east-1` |  |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |

## Authentication

All endpoints except `/health` and `/webhook` require
`Authorization: Bearer <jwt>`. The token must be HS256-signed with
`JWT_SECRET` and carry the documented Auth-service payload (`sub`,
`email`, `jti`, `iss`, `aud`, `iat`, `exp`).

## DynamoDB schema (event-log shape)

The table is provisioned out-of-band by Terraform. This service writes
records with this shape:

- `pk = "USER#" + user_id`
- `sk = "EVENT#" + ulid`
- attributes: `event_id`, `user_id`, `event_type`, `created_at` plus
  type-specific extras (`tier`, `seats`, `stripe_session_id`,
  `stripe_customer_id`, `stripe_subscription_id`, `status`,
  `current_period_end`, `stripe_price_id`).

Event types written:
- `checkout_started` (caller hit `/checkout-session`)
- `customer_created` (caller hit `/portal-session` with no customer on file)
- `checkout_completed` (Stripe `checkout.session.completed`)
- `subscription_created` (Stripe `customer.subscription.created`)
- `subscription_updated` (Stripe `customer.subscription.updated`)
- `subscription_deleted` (Stripe `customer.subscription.deleted`)
- `invoice_paid` (Stripe `invoice.paid`)
- `invoice_payment_failed` (Stripe `invoice.payment_failed`)

## Stripe webhook registration

In the Stripe Dashboard, register a webhook endpoint pointing at the
deployed billing service's webhook route. The in-process path is
`/webhook`; behind the dev API Gateway the public path is:

```
https://<api-gateway-host>/dev/v1/billing/webhook
```

(The `/v1/billing/{proxy+}` shape is set by `infra/dev/api-gateway`
per ADR-038, and the proxy-strip removes the `/v1/billing/` segment so
the service sees `/webhook`. For local-dev, hit
`http://localhost:8000/webhook`.)

Subscribe to exactly these five events. Anything else returns 200
`{"status": "ignored"}` so Stripe stops retrying:

1. `checkout.session.completed`
2. `customer.subscription.created`
3. `customer.subscription.updated`
4. `customer.subscription.deleted`
5. `invoice.payment_failed`

(`invoice.paid` is also handled for payment-success audit but is not in
the minimal-required set; subscribe to it if you want a full audit
trail.)

Copy the resulting `whsec_...` signing secret from the Dashboard into
the `panakoes-dev/stripe-webhook-signing-secret` AWS Secrets Manager
secret. The ECS task injects it into the container as
`STRIPE_WEBHOOK_SECRET`.

## Subscription state table

Webhook events that change subscription state also project onto the
`panakoes-dev-subscriptions` DynamoDB table (provisioned by
`infra/dev/data/main.tf`):

- `pk = tenant_id` (Panakoes user / tenant id)
- `sk = subscription_id` (Stripe `sub_...` id)
- attributes: `plan` (`free` | `pro` | `team`), `status`,
  `current_period_end`, `cancel_at`, `quantity`,
  `stripe_customer_id`, `last_event_id`, `updated_at`.

The Auth service reads this table at sign-in time and bakes the
resulting plan into the issued JWT's `plan` claim. Downstream services
use `panakoes_middleware.require_plan("pro")` to gate routes (see
contract below).

## Idempotency

Stripe retries webhooks on any 5xx. The webhook handler writes to the
subscriptions table via a conditional `PutItem`
(`attribute_not_exists(last_event_id) OR last_event_id <> :evt`), so a
replay of the same Stripe `evt_...` id is a no-op: the response is
`{"status": "duplicate", "stripe_event_id": "<id>"}` and no extra row
is written to the event log either.

## 402 Payment Required plan-gating contract

The Billing service publishes the plan into the JWT; downstream
services enforce the gate via `panakoes_middleware.require_plan`. The
contract is:

| JWT plan claim | `require_plan("pro")` | `require_plan("team")` |
| ---            | ---                   | ---                    |
| (missing)      | 402                   | 402                    |
| `free`         | 402                   | 402                    |
| `pro`          | 200                   | 402                    |
| `team`         | 200                   | 200                    |

402 response body:
```json
{
  "detail": {
    "detail": "plan_required",
    "required_plan": "pro",
    "current_plan": "free"
  }
}
```

Unknown or malformed plan claims are coerced to `free` (never silently
upgraded). Requests without a verified JWT principal raise 401 instead
of 402, since authentication is the precondition for plan-gating.

The `GET /subscription` endpoint returns the most recent event
that carries subscription state. Append-only writes keep the audit
trail honest and the next slice projects a materialized view for O(1)
reads.

## Audit

Every meaningful billing action is also recorded via the
`panakoes-audit` library with `source_service="billing"` and one of:

- `billing.checkout_started`
- `billing.subscription_changed`
- `billing.payment_succeeded`
- `billing.payment_failed`
- `billing.webhook_signature_invalid`

## Running locally

```bash
uv sync --group dev
uv run uvicorn panakoes_billing.main:app --reload
```

## Running tests

```bash
uv run pytest
```

Coverage threshold for this service is 100% per ADR-018 (billing is
security-critical). Webhook signature verification has explicit
positive and negative tests; the `Settings` validator that forbids
live Stripe keys at startup is also tested.

## Linting and type checking

```bash
uv run ruff check
uv run mypy src
```

## Building the Docker image

Canonical bake path is GitHub Actions (`.github/workflows/image-bake-on-change.yml` on push to `main`, or the `image-bake-manual.yml` one-button workflow). The local command below is a fallback for offline dev.

```bash
docker build -t panakoes-billing .
```

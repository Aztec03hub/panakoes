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
| POST   | `/billing/checkout-session`| Bearer | Create a Stripe Checkout Session for `pro` or `team` |
| POST   | `/billing/portal`          | Bearer | Create a Stripe Customer Portal session for the JWT subject |
| POST   | `/billing/webhook`         | Stripe-Signature | Handle Stripe webhooks (signature-verified) |
| GET    | `/billing/subscription`    | Bearer | Return the JWT subject's current subscription view |

The webhook endpoint is intentionally unauthenticated at the JWT
layer: Stripe does not send a Panakoes JWT. Authentication is the
`Stripe-Signature` header verified against `STRIPE_WEBHOOK_SECRET`.

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
| `AWS_REGION` | `us-east-1` |  |
| `AUDIT_BACKEND` | `stdout` | Set to `dynamodb` in production |

## Authentication

All endpoints except `/health` and `/billing/webhook` require
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
- `checkout_started` (caller hit `/billing/checkout-session`)
- `checkout_completed` (Stripe `checkout.session.completed`)
- `subscription_updated` (Stripe `customer.subscription.updated`)
- `subscription_deleted` (Stripe `customer.subscription.deleted`)
- `invoice_paid` (Stripe `invoice.paid`)
- `invoice_payment_failed` (Stripe `invoice.payment_failed`)

The `GET /billing/subscription` endpoint returns the most recent event
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

```bash
docker build -t panakoes-billing .
```

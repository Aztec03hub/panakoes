# Dev Environment API Gateway

Per-environment Terraform configuration creating the public-facing
AWS API Gateway v2 HTTP API (`panakoes-dev-public`) that fronts every
Panakoes microservice. Consumes the S3 remote state backend created
by `infra/bootstrap/`; state lives at
`dev/api-gateway/terraform.tfstate`.

## What this creates

- `aws_apigatewayv2_api.main` (HTTP API named `panakoes-dev-public`)
  with CORS configured for the production marketing domain, the
  LaFayette Labs site, and the local Vite dev server.
- `aws_apigatewayv2_vpc_link.main` (shared VPC Link spanning all
  three private subnets) and the security group attached to its
  elastic network interfaces.
- Zero or more `aws_apigatewayv2_integration.service_proxy` resources,
  one per upstream service whose NLB listener ARN has been discovered
  via the ECS module's `nlb_listener_arns` remote-state output (see
  "Incremental rollout" below). Each proxy integration strips the
  `/v1/<service>/` prefix via `overwrite:path = "/$request.path.proxy"`
  before forwarding to the backend NLB.
- Zero or more `aws_apigatewayv2_integration.service_override` resources,
  one per explicit override route defined in `local.explicit_overrides`.
  Each override integration carries a literal `overwrite:path` so the
  backend sees the canonical path (e.g. `/sign-up`).
- One `aws_apigatewayv2_route.service_proxy` per discovered service at
  `ANY /v1/<service>/{proxy+}` (the catch-all default).
- Zero or more `aws_apigatewayv2_route.service_override` for routes
  that need per-route policy (throttling today; distinct authorizer or
  observability dimension future). API Gateway HTTP API v2 picks the
  more-specific override over the catch-all at request time.
- `aws_apigatewayv2_stage.main` named `dev`, auto-deploy on,
  throttling burst 5000 / rate 10000, structured-JSON access logs to
  CloudWatch.
- `aws_cloudwatch_log_group.access` at `/aws/apigatewayv2/panakoes-dev`
  with 30-day retention and KMS encryption.
- A local CMK (`alias/panakoes-dev-api-gateway-logs`) that encrypts
  the log group. Replaced by the dev-observability CMK once
  `infra/dev/observability/` lands.
- `aws_wafv2_web_acl_association.main` linking the stage to the
  panakoes-dev-public-acl from `infra/dev/waf/`. Conditional on the
  WAF state being present (`try()`); the next apply after
  `infra/dev/waf/` is applied creates the association.
- Three `aws_cloudwatch_metric_alarm` resources covering the
  standard front-door SLOs (4xx rate, 5xx rate, integration latency
  p99).

## Routing strategy: (c+) proxy default with explicit overrides

Per [ADR-038](../../../docs/adr/038-api-gateway-routing-strategy.md),
the routing surface follows two layered rules:

1. **Default: per-service proxy catch-all.** Every service whose NLB
   listener ARN appears in the discovered map automatically gets one
   route at `ANY /v1/<service>/{proxy+}`. The proxy integration strips
   the `/v1/<service>/` prefix; the backend service sees canonical
   paths and owns its routing internally. Adding a new endpoint inside
   a service is a service-code change with **zero infra PR required**.
2. **Explicit overrides where policy demands it.** Routes that need
   per-route throttling, a distinct authorizer, or a distinct
   CloudWatch dimension are layered on top via
   `local.explicit_overrides` in `main.tf`. API Gateway HTTP API v2
   picks the more-specific route at request time.

### Public surface today

Per-service catch-alls (one per discovered service):

| Route key | Backend | Notes |
|-----------|---------|-------|
| `ANY /v1/auth/{proxy+}` | auth NLB | Unauthenticated by default; service-side auth |
| `ANY /v1/ingestion-api/{proxy+}` | ingestion-api NLB | Service JWT |
| `ANY /v1/summarization/{proxy+}` | summarization NLB | Service JWT |
| `ANY /v1/notification/{proxy+}` | notification NLB | Service JWT |
| `ANY /v1/query-api/{proxy+}` | query-api NLB | Service JWT |
| `ANY /v1/session-manager/{proxy+}` | session-manager NLB | Service JWT |
| `ANY /v1/billing/{proxy+}` | billing NLB | Service JWT (Stripe webhook is Stripe-signed) |
| `ANY /v1/cost-api/{proxy+}` | cost-api NLB | Service JWT |
| `ANY /v1/admin-api/{proxy+}` | admin-api NLB | Service JWT |

Explicit overrides:

| Route key | Backend path | Throttle | Why |
|-----------|--------------|----------|-----|
| `POST /v1/auth/sign-up` | `/sign-up` | 5 req/sec, burst 10 | Anti-enumeration |
| `POST /v1/auth/sign-in` | `/sign-in` | 10 req/sec, burst 20 | Anti-brute-force |

"Service JWT" means the upstream service validates the JWT itself.
The HTTP API does not yet attach a gateway-layer JWT authorizer;
promotion to gateway-layer auth is a deliberate follow-up so the auth
path is reviewed in isolation.

### Adding a new explicit override

1. Confirm the service's NLB listener ARN is in
   `local.service_nlb_listener_arns` (otherwise the override is
   filtered out as inactive). The proxy catch-all already covers the
   route; you only need an override if per-route policy applies.
2. Add an entry to `local.explicit_overrides` in `main.tf`:

   ```hcl
   "POST /v1/<service>/<public-path>" = {
     service      = "<service>"
     backend_path = "/<canonical-backend-path>"
     throttle     = { burst_limit = 20, rate_limit = 10 } # or null
   }
   ```

3. `terraform fmt && terraform validate && terraform plan`. The plan
   should show one new integration, one new route, and (if `throttle`
   is non-null) one additional `route_settings` block on the stage.
4. Apply. The override takes effect on the next stage auto-deploy.

### Adding a new endpoint that does NOT need an override

Do nothing in this module. Add the endpoint in the service's own
code; the `ANY /v1/<service>/{proxy+}` catch-all already routes it.
This is the whole point of the (c+) shape.

## Why HTTP API and not REST API

HTTP APIs ship the features Panakoes needs (JWT authorizers, CORS,
VPC Link integrations) at roughly one third the per-million-request
cost of REST APIs. We do not need REST API features: no request /
response transformations, no API keys with usage plans, no client
certificate authentication, no SDK generation. HTTP API was built as
the lower-friction successor; choose it unless a feature forces the
older surface.

## Incremental rollout

The module is services-first incremental and idempotent. It applies
cleanly with zero, one, or all 7 backend ECS services in flight.

How it works:

- `data.terraform_remote_state.ecs` reads the ECS module's state
  (key `dev/ecs/terraform.tfstate`).
- `local.service_nlb_listener_arns = try(data.terraform_remote_state.ecs.outputs.nlb_listener_arns, {})`
  returns an empty map when the ECS state does not exist yet (or
  when the output is absent).
- `aws_apigatewayv2_integration.service` and
  `aws_apigatewayv2_route.service` are both `for_each`-driven over
  the discovered map. With zero NLBs, both resource sets are empty;
  the HTTP API + VPC Link + stage + access log + alarms still come
  up so the routing surface is live.
- As each ECS service exposes its NLB listener ARN under the agreed
  output name, the corresponding integration + routes appear
  automatically on the next `terraform apply`. No hand-edits to this
  module.

ECS module contract (the ECS module(s) MUST conform):

```hcl
output "nlb_listener_arns" {
  description = "Map of service name to NLB listener ARN."
  value = {
    auth          = aws_lb_listener.auth.arn
    ingestion-api = aws_lb_listener.ingestion_api.arn
    # one entry per service that has an NLB listener provisioned
  }
}
```

Service-name keys MUST match the `service` field in
`local.routes` (see `main.tf`): `auth`, `ingestion-api`,
`summarization`, `notification`, `query-api`, `session-manager`,
`billing`. Any service key in the map that is not referenced by a
route still gets an integration provisioned (harmless, just unused);
any route referencing a service that is not in the map is silently
skipped until the service ships.

## Stripe webhook is unauthenticated

`POST /billing/webhook` skips gateway authentication because Stripe
signs the request body with the shared `stripe_webhook_signing`
secret; the billing service validates the signature on receipt.
Forcing a JWT here would block Stripe.

## CloudWatch alarms

| Alarm | Threshold | Window | Use |
|-------|-----------|--------|-----|
| 4xx rate | > 10% | 10 min | Sustained client errors. Look at it. |
| 5xx rate | > 1% | 5 min | Gateway / upstream failing. Page on-call. |
| Integration latency p99 | > 2000 ms | 5 min | Upstream service slowing. Investigate before users notice. |

Alarm action targets (SNS topic, on-call PagerDuty integration) are
not yet provisioned; alarms emit ALARM-state CloudWatch events that
downstream notification wiring can consume.

## Custom domain (deferred)

`api.panakoes.com` is the planned production hostname. Provisioning
requires an ACM certificate covering the domain in `us-east-1` plus
a Route 53 (or external DNS) record set; neither exists today. The
custom-domain skeleton lives in `main.tf` as a commented-out block,
and the inputs (`custom_domain_name`, `custom_domain_certificate_arn`)
are wired through `variables.tf`. Enabling the custom domain becomes
a Terraform variable change instead of a structural rewrite.

## Apply (when ready)

    cd infra/dev/api-gateway
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

Module is committed but not yet applied. `terraform init` produces
the lock file; `terraform plan` and `apply` require AWS credentials
and remain a deliberate follow-up.

## Variables

| Variable | Type | Default | Purpose |
|---|---|---|---|
| `aws_region` | string | `us-east-1` | Region for the API Gateway and CloudWatch resources |
| `environment` | string | `dev` | Environment name |
| `project_name` | string | `panakoes` | Project name |
| `stage_name` | string | `dev` | Stage name |
| `cors_allow_origins` | list(string) | (3 origins) | CORS allow-origins list |
| `cors_allow_methods` | list(string) | GET, POST, PATCH, DELETE, OPTIONS | CORS methods |
| `cors_allow_headers` | list(string) | Authorization, Content-Type, X-Request-Id | CORS headers |
| `throttling_burst_limit` | number | 5000 | Stage burst limit |
| `throttling_rate_limit` | number | 10000 | Stage steady-state rate |
| `access_log_retention_days` | number | 30 | Access log group retention |
| `vpc_link_subnet_count` | number | 3 | Private subnets the VPC Link spans |
| `alarm_period_seconds` | number | 60 | CloudWatch alarm period |
| `alarm_4xx_threshold_percent` | number | 10 | 4xx-rate alarm threshold |
| `alarm_5xx_threshold_percent` | number | 1 | 5xx-rate alarm threshold |
| `alarm_p99_latency_ms` | number | 2000 | Integration latency p99 threshold |
| `custom_domain_name` | string | `""` | Custom domain (skeleton; deferred) |
| `custom_domain_certificate_arn` | string | `""` | ACM cert for custom domain (skeleton; deferred) |

## Outputs

| Output | Type | Purpose |
|--------|------|---------|
| `api_id` | string | HTTP API ID |
| `api_arn` | string | HTTP API ARN |
| `api_endpoint` | string | Default `<api-id>.execute-api.<region>.amazonaws.com` endpoint |
| `stage_name` | string | Deployed stage name |
| `stage_invoke_url` | string | Full invoke URL for the deployed stage |
| `stage_arn` | string | Stage ARN |
| `vpc_link_id` | string | Shared VPC Link ID |
| `vpc_link_arn` | string | Shared VPC Link ARN |
| `vpc_link_security_group_id` | string | Security group on the VPC Link ENIs |
| `access_log_group_name` | string | Access log group name |
| `access_log_group_arn` | string | Access log group ARN |
| `kms_key_arn` | string | CMK ARN encrypting the access log group |

## Access log fields and the 5xx diagnostic playbook

The stage emits one structured JSON row per request to
`/aws/apigatewayv2/panakoes-dev`. Field groups:

| Group | Fields | Use |
|---|---|---|
| Identity | `requestId`, `requestTime`, `sourceIp`, `userAgent` | Correlate with backend logs |
| Route | `httpMethod`, `path`, `routeKey` | Tell "no route matched" (no `routeKey`) from "route matched, backend failed" |
| Response | `status`, `protocol`, `responseLength` | Final response seen by the client |
| Integration | `integrationRequestId`, `integrationStatus`, `integrationLatency`, `integrationErrorMessage` | Distinguish upstream-5xx from integration-timeout from VPC-link-refused |
| Gateway error | `errorMessage`, `errorResponseType` | Populated when API Gateway itself rejects (auth, throttle, integration failure) |
| Authorizer | `authorizerError`, `authorizerLatency` | Forward-wired for the first JWT-authorizer addition |

Canonical 5xx signatures:

- `status = 503` with `integrationLatency = 29000`: upstream
  handler hung the full 29s integration timeout. Look in the
  upstream service log group for a request that started but never
  emitted its success log line.
- `status = 5xx` with `integrationStatus = 5xx`: upstream returned
  the 5xx itself; the gateway forwarded it unchanged.
- `status = 5xx` with `integrationStatus = "-"` and
  `errorResponseType` populated: API Gateway rejected the request
  before reaching the backend (most commonly `INTEGRATION_FAILURE`
  for misconfigured integrations or `INTERNAL_SERVER_ERROR` for
  gateway-side issues).
- `status = 401 / 403` with `authorizerError` populated: JWT
  authorizer rejected the token. Only meaningful once a route
  carries `authorization_type = JWT`.

CloudWatch Logs Insights query to surface 5xx rows with the
diagnostic fields visible:

```
fields @timestamp, requestId, routeKey, path, status,
       integrationStatus, integrationLatency, errorMessage
| filter status >= 500
| sort @timestamp desc
| limit 50
```

Note: HTTP API v2 does NOT expose `$context.rawQueryString` in
access log variables. The UpdateStage API rejects it with
`BadRequestException: The following context variables are not
supported: [$context.rawQueryString]`. Query strings can only be
recovered via header capture or integration-side logging.

## Consuming outputs from other configs

Downstream modules read these outputs via a `terraform_remote_state`
data source pointing at this config's state:

```hcl
data "terraform_remote_state" "api_gateway" {
  backend = "s3"
  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/api-gateway/terraform.tfstate"
    region = "us-east-1"
  }
}

# Then reference outputs as:
#   data.terraform_remote_state.api_gateway.outputs.api_id
#   data.terraform_remote_state.api_gateway.outputs.stage_invoke_url
#   data.terraform_remote_state.api_gateway.outputs.vpc_link_id
```

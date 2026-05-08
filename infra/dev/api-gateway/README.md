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
- One `aws_apigatewayv2_integration` per upstream service
  (`auth`, `ingestion-api`, `summarization`, `notification`,
  `query-api`, `session-manager`, `billing`) wired to placeholder NLB
  listener ARNs (see "Placeholder NLB ARNs" below).
- 25 `aws_apigatewayv2_route` resources covering the route surface
  documented in the table below, plus a public `GET /health` route
  served by an inline MOCK integration.
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

## Route surface

| Method | Path | Service | Auth |
|--------|------|---------|------|
| POST | /auth/sign-up | auth | unauthenticated |
| POST | /auth/sign-in | auth | unauthenticated |
| POST | /auth/sign-out | auth | service JWT |
| POST | /auth/validate | auth | unauthenticated |
| POST | /ingestion/audio | ingestion-api | service JWT |
| GET  | /ingestion/{id} | ingestion-api | service JWT |
| GET  | /ingestion | ingestion-api | service JWT |
| POST | /summarize | summarization | service JWT |
| GET  | /summary/{id} | summarization | service JWT |
| GET  | /summaries | summarization | service JWT |
| POST | /notify/email | notification | service JWT |
| POST | /notify/webhook | notification | service JWT |
| GET  | /notifications | notification | service JWT |
| GET  | /notifications/{id} | notification | service JWT |
| GET  | /transcripts | query-api | service JWT |
| GET  | /transcripts/{id} | query-api | service JWT |
| GET  | /sessions | query-api | service JWT |
| GET  | /sessions/{id} | query-api | service JWT |
| POST | /sessions | session-manager | service JWT |
| PATCH | /sessions/{id} | session-manager | service JWT |
| DELETE | /sessions/{id} | session-manager | service JWT |
| POST | /billing/checkout-session | billing | service JWT |
| POST | /billing/portal | billing | service JWT |
| GET  | /billing/subscription | billing | service JWT |
| POST | /billing/webhook | billing | unauthenticated (Stripe-signed body) |
| GET  | /health | (mock) | unauthenticated |

"Service JWT" means the upstream service validates the JWT itself
(see `services/*/auth.py`). The HTTP API does not yet attach a
gateway-layer JWT authorizer; promotion to gateway-layer auth is a
deliberate follow-up so the auth path is reviewed in isolation.

## Why HTTP API and not REST API

HTTP APIs ship the features Panakoes needs (JWT authorizers, CORS,
VPC Link integrations) at roughly one third the per-million-request
cost of REST APIs. We do not need REST API features: no request /
response transformations, no API keys with usage plans, no client
certificate authentication, no SDK generation. HTTP API was built as
the lower-friction successor; choose it unless a feature forces the
older surface.

## Placeholder NLB ARNs

API Gateway HTTP API VPC Link integrations require an NLB listener
ARN as the integration target. The Network Load Balancers do not
exist yet (no ECS service has been deployed). `main.tf` constructs
listener ARNs in the AWS-documented format with placeholder load
balancer and listener IDs:

```
arn:aws:elasticloadbalancing:us-east-1:<account>:listener/net/panakoes-dev-<service>/0000000000000000/0000000000000000
```

`terraform apply` succeeds because API Gateway does not validate the
target NLB at create time; requests routed through the integrations
return 503 from the VPC Link until real NLBs land. This is
intentional: provision the routing surface first, attach real
targets second. When the ECS / NLB module ships, swap the
`local.service_nlb_listener_arns` map to a `terraform_remote_state`
output.

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

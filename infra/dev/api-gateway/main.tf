locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "api-gateway"
  }

  name_prefix = "${var.project_name}-${var.environment}"
  api_name    = "${local.name_prefix}-public"

  # ---------------------------------------------------------------------
  # Network references
  #
  # Pulled into locals so each downstream resource reads a short name
  # rather than walking through the remote-state attribute chain.
  # ---------------------------------------------------------------------
  vpc_id             = data.terraform_remote_state.network.outputs.vpc_id
  private_subnet_ids = data.terraform_remote_state.network.outputs.private_subnet_ids

  # ---------------------------------------------------------------------
  # WAF web ACL (optional)
  #
  # `try()` returns null when `dev/waf/` has not yet been applied. The
  # `aws_wafv2_web_acl_association` resource conditions its existence
  # on this value; absence keeps the API Gateway unprotected by WAF
  # only until the WAF module lands, at which point the next apply
  # creates the association.
  # ---------------------------------------------------------------------
  web_acl_arn = try(data.terraform_remote_state.waf.outputs.web_acl_arn, null)

  # ---------------------------------------------------------------------
  # Log-group KMS key
  #
  # When the dedicated observability module ships, it will own the
  # CMK that encrypts every CloudWatch log group in the dev account.
  # Until then, we provision a local CMK in this module (see
  # `aws_kms_key.api_gateway_logs` below). The `try()` wrapper
  # surfaces the future remote-state output the moment it exists; at
  # that point the local key can be retired in a follow-up PR.
  # ---------------------------------------------------------------------
  observability_kms_key_arn = try(
    data.terraform_remote_state.observability.outputs.log_kms_key_arn,
    null,
  )

  log_kms_key_arn = coalesce(
    local.observability_kms_key_arn,
    aws_kms_key.api_gateway_logs.arn,
  )

  # ---------------------------------------------------------------------
  # Service NLB listener ARNs (kept for reference; no longer used for
  # proxy_services after Wave 1 ALB rewire)
  #
  # The per-NLB ARN map used by the original (pre-Wave1) integration
  # strategy. Retained here because `service_override` integrations
  # still reference `local.service_nlb_listener_arns` for services
  # in `active_overrides`. When Wave 1 is fully applied and
  # explicit_overrides remains empty (as it is now), this can be
  # removed in a follow-up cleanup PR.
  # ---------------------------------------------------------------------
  service_nlb_listener_arns = (
    var.discover_ecs_nlbs && length(data.terraform_remote_state.ecs) > 0
    ? try(data.terraform_remote_state.ecs[0].outputs.nlb_listener_arns, {})
    : {}
  )

  # ---------------------------------------------------------------------
  # ALB listener ARN (Wave 1 shared ALB, replaces per-NLB integrations)
  # ---------------------------------------------------------------------
  alb_listener_arn = data.terraform_remote_state.alb.outputs.listener_arn

  # The 8 public services routed through the shared ALB. Keys must match
  # the route path segment (/v1/<key>/{proxy+}) and the
  # X-Panakoes-Service header.
  # summarization, notification, gpu-spawner are internal-only (Service
  # Connect only); they get no API GW route.
  alb_public_services = toset([
    "auth",
    "admin-api",
    "cost-api",
    "health-aggregator",
    "ingestion-api",
    "query-api",
    "session-manager",
    "billing",
  ])

  # ---------------------------------------------------------------------
  # Routing strategy: (c+) proxy catch-all per service + explicit overrides
  #
  # Per ADR-038, this module's routing shape is:
  #   1. One `ANY /v1/<service>/{proxy+}` catch-all per discovered
  #      service. Service teams own their public surface end-to-end:
  #      adding a new endpoint is a service-code change with zero
  #      Terraform PR.
  #   2. Zero or more EXPLICIT OVERRIDE routes layered on top of the
  #      catch-all when a route needs per-route policy (throttling,
  #      observability dimension, distinct authorizer). API Gateway
  #      HTTP API v2 picks the more-specific route at request time, so
  #      the catch-all and the override coexist on the same backend.
  #
  # Both shapes target the shared ALB via the X-Panakoes-Service header.
  # The ALB listener rules match on this header to route to the correct
  # service target group. Services continue to receive stripped paths
  # (/$request.path.proxy), so no service code changes are needed.
  # ---------------------------------------------------------------------

  # Each service in `alb_public_services` automatically gets a proxy
  # catch-all route at `ANY /v1/<service>/{proxy+}`. The map value is
  # just the key itself (used as the X-Panakoes-Service header value).
  proxy_services = { for svc in local.alb_public_services : svc => svc }

  # ---------------------------------------------------------------------
  # Explicit override routes
  #
  # Use this map only when a route needs per-route policy that the
  # default proxy catch-all cannot express:
  #   - `throttle` non-null: sets per-route throttling via
  #     `route_settings` on the stage (see below).
  #   - Future fields (authorizer, distinct CloudWatch dimension) plug
  #     in here without changing the catch-all shape.
  #
  # The map key is the public route key (`METHOD /v1/<service>/<path>`).
  # `service` MUST match a key in `service_nlb_listener_arns`.
  # `backend_path` is the LITERAL path forwarded to the backend after
  # the gateway strips the `/v1/<service>/` prefix. The backend is
  # expected to mount its handler at this path.
  #
  # NOTE: previously held `POST /v1/auth/sign-up` and
  # `POST /v1/auth/sign-in` with per-route throttling. Those entries
  # were removed because an explicit `POST` route on an HTTP API does
  # NOT auto-create a sibling `OPTIONS` route, so browser CORS
  # preflight (`OPTIONS /v1/auth/sign-in` before any cross-origin POST
  # with `Content-Type: application/json`) fell through to the
  # gateway's default 404 handler and blocked every browser login
  # attempt. The `ANY /v1/auth/{proxy+}` catch-all handles all methods
  # (GET / POST / OPTIONS / etc.) including the preflight, and the
  # HTTP API's `cors_configuration` block attaches the right
  # `Access-Control-Allow-*` headers to the preflight response
  # automatically.
  #
  # Trade-off: sign-up and sign-in lose their dedicated per-route
  # throttle (5 r/s and 10 r/s respectively) and inherit the stage
  # default. Acceptable in dev where WAF rate-rules are the primary
  # anti-enumeration / anti-brute-force defence; if per-route caps
  # become necessary again the cleanest path is mock `OPTIONS`
  # siblings (plan Option B) rather than re-introducing explicit POST
  # routes alone. See `.agent-runs/<timestamp>-fix-cors-auth-preflight.md`
  # and the admin-panel E2E fix plan for context.
  # ---------------------------------------------------------------------
  explicit_overrides = {}

  # Active overrides: filter to services whose NLB has been discovered
  # via remote state. Mirrors the proxy filter so a service that has
  # not yet shipped its NLB doesn't break the apply with a dangling
  # integration target.
  active_overrides = {
    for route_key, override in local.explicit_overrides :
    route_key => override
    if contains(keys(local.service_nlb_listener_arns), override.service)
  }
}

# ---------------------------------------------------------------------------
# CloudWatch log-group KMS key (local fallback)
#
# Provisions a customer-managed key dedicated to the API Gateway
# access log group. When the planned `infra/dev/observability/` module
# lands, its CMK will replace this one (see `local.log_kms_key_arn`
# coalescence). The key policy delegates encrypt rights to the
# regional CloudWatch Logs service principal, scoped to this log
# group's ARN to prevent unrelated log groups from piggybacking on
# the same key.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "api_gateway_logs_kms" {
  statement {
    sid     = "EnableRootAccountAdmin"
    effect  = "Allow"
    actions = ["kms:*"]

    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy document. `Resource: *` here refers to the single key
    # this document is attached to (`aws_kms_key.api_gateway_logs`); AWS
    # resolves `*` against the owning key only when the policy is attached.
    # Tightening to a specific ARN would be circular (key ARN does not exist
    # at policy-document evaluation time).
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
  }

  statement {
    sid    = "AllowCloudWatchLogsEncrypt"
    effect = "Allow"
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]

    principals {
      type        = "Service"
      identifiers = ["logs.${var.aws_region}.amazonaws.com"]
    }

    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` resolves to this key only. Service-principal use
    # is additionally constrained by the EncryptionContext condition below
    # pinning encryption to this module's log group ARN.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]

    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/apigatewayv2/${local.name_prefix}"]
    }
  }
}

resource "aws_kms_key" "api_gateway_logs" {
  description             = "KMS key for the dev API Gateway access log group (placeholder until observability module lands)."
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.api_gateway_logs_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "api_gateway_logs" {
  name          = "alias/${local.name_prefix}-api-gateway-logs"
  target_key_id = aws_kms_key.api_gateway_logs.key_id
}

# ---------------------------------------------------------------------------
# CloudWatch log group for API Gateway access logs
#
# 30-day retention matches the project default. Long-term archive is
# the job of a downstream subscription filter to the log-archive S3
# bucket (planned, not yet provisioned).
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/apigatewayv2/${local.name_prefix}"
  retention_in_days = var.access_log_retention_days
  kms_key_id        = local.log_kms_key_arn

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# VPC Link security group
#
# API Gateway HTTP API VPC Links require a security group attached to
# the link's elastic network interfaces. The link initiates outbound
# TCP to the upstream NLB targets, so we allow all egress within the
# VPC CIDR. Ingress is empty: nothing originates traffic toward the
# VPC Link itself.
# ---------------------------------------------------------------------------

resource "aws_security_group" "vpc_link" {
  name        = "${local.name_prefix}-api-gateway-vpc-link"
  description = "Security group for the API Gateway HTTP API VPC Link ENIs."
  vpc_id      = local.vpc_id

  egress {
    description = "Allow VPC Link to reach NLB listeners over the VPC CIDR."
    from_port   = 0
    to_port     = 65535
    protocol    = "tcp"
    cidr_blocks = [data.terraform_remote_state.network.outputs.vpc_cidr_block]
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-api-gateway-vpc-link"
  })
}

# ---------------------------------------------------------------------------
# VPC Link
#
# A single shared VPC Link covers every backend service. AWS supports
# up to 10 VPC Links per account per region; provisioning one per
# service would burn that quota with no benefit since all services
# live in the same VPC and the security group is identical.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_vpc_link" "main" {
  name               = "${local.name_prefix}-vpc-link"
  security_group_ids = [aws_security_group.vpc_link.id]
  subnet_ids         = slice(local.private_subnet_ids, 0, var.vpc_link_subnet_count)

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# HTTP API
#
# Protocol type HTTP gets us the v2 HTTP API (lower cost than REST,
# native CORS, JWT authorizer support). CORS configuration is enforced
# at the gateway layer so per-service code does not have to repeat it.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "main" {
  name          = local.api_name
  protocol_type = "HTTP"
  description   = "Public HTTP API fronting every Panakoes microservice in the dev environment."

  cors_configuration {
    allow_origins     = var.cors_allow_origins
    allow_methods     = var.cors_allow_methods
    allow_headers     = var.cors_allow_headers
    allow_credentials = true
    max_age           = 600
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Per-service PROXY integrations (catch-all default)
#
# One integration per upstream service whose NLB listener ARN has
# been discovered via the ECS module's remote state. These integrations
# back the `ANY /v1/<service>/{proxy+}` catch-all routes and rewrite
# the forwarded path to the captured `{proxy+}` segment so backends
# see canonical paths (e.g. `/health` not `/v1/auth/health`).
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_integration" "service_proxy" {
  for_each = local.proxy_services

  api_id             = aws_apigatewayv2_api.main.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.main.id

  # Wave 1: all services share the single ALB listener. The ALB routes
  # to the correct service target group based on the X-Panakoes-Service
  # header injected below (ADR-Wave1-ALB). NLB ARNs are no longer used.
  integration_uri = local.alb_listener_arn

  payload_format_version = "1.0"
  timeout_milliseconds   = 29000

  # Strip the /v1/<service>/ prefix before forwarding. The
  # `$request.path.proxy` reference resolves to the value captured by
  # the route key's `{proxy+}` greedy segment. Without this, backend
  # services would receive the full `/v1/auth/health` path and return
  # 404 because their routes are mounted at root (`/health`).
  #
  # The X-Panakoes-Service header tells the shared ALB which service
  # target group to route to. The ALB listener rules match on this
  # header value (each.key) instead of the incoming path, which lets
  # the gateway continue stripping the /v1/<service>/ prefix while
  # the ALB still dispatches to the right backend.
  request_parameters = {
    "overwrite:path"                      = "/$request.path.proxy"
    "overwrite:header.x-panakoes-service" = each.key
  }
}

# ---------------------------------------------------------------------------
# Per-override integrations (explicit override routes)
#
# Per-route `request_parameters` are not exposed on
# `aws_apigatewayv2_route`; the path-rewrite has to live on the
# integration. Each explicit override therefore gets its OWN
# integration whose `overwrite:path` is the literal stripped backend
# path (e.g. `/sign-up`). The integration still targets the same
# backend NLB as the service's proxy catch-all; only the path-mapping
# differs. API Gateway picks the more-specific override route at
# request time, so requests for `POST /v1/auth/sign-up` flow through
# THIS integration while every other `/v1/auth/*` path flows through
# the proxy catch-all integration.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_integration" "service_override" {
  for_each = local.active_overrides

  api_id             = aws_apigatewayv2_api.main.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.main.id
  integration_uri    = local.service_nlb_listener_arns[each.value.service]

  payload_format_version = "1.0"
  timeout_milliseconds   = 29000

  request_parameters = {
    "overwrite:path" = each.value.backend_path
  }
}

# ---------------------------------------------------------------------------
# Proxy catch-all routes (default for every discovered service)
#
# `ANY /v1/<service>/{proxy+}` forwards every method + sub-path under
# `/v1/<service>/` to that service's proxy integration. Service teams
# add new endpoints inside their own service code with no Terraform
# PR required (per ADR-038).
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "service_proxy" {
  for_each = local.proxy_services

  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /v1/${each.key}/{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.service_proxy[each.key].id}"

  authorization_type = "NONE"
}

# ---------------------------------------------------------------------------
# Explicit override routes
#
# Layered on top of the proxy catch-all when a route needs per-route
# policy (throttling today; distinct authorizer / observability
# dimension future). API Gateway HTTP API v2's route-matching prefers
# the more-specific route, so these win over the catch-all at request
# time. Per-route throttling is wired in `aws_apigatewayv2_stage.main`
# below via `route_settings` blocks.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "service_override" {
  for_each = local.active_overrides

  api_id    = aws_apigatewayv2_api.main.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.service_override[each.key].id}"

  authorization_type = "NONE"
}

# ---------------------------------------------------------------------------
# Public health check (intentionally NOT defined here)
#
# API Gateway v2 (HTTP API) does not support `MOCK` integrations
# (REST API v1 only); HTTP API only allows `AWS_PROXY` and
# `HTTP_PROXY`. A MOCK-based gateway-level health endpoint would
# force a Lambda dependency that adds cost + cold-start latency for
# marginal value.
#
# Each downstream service exposes its own `/health` (or `/healthz`)
# endpoint inside its own application code, reachable through its
# proxy route at `GET /v1/<service>/health`. Aggregate health checks
# are better expressed as CloudWatch synthetics canaries against the
# service-owned endpoints, not as a fake gateway response.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Stage
#
# Auto-deploy keeps the stage in sync with route / integration changes
# without manual deployments. Throttling caps the burst and steady
# rate per the variables; downstream services should never see a
# request rate higher than these numbers from this gateway.
# Access logging emits a structured JSON record per request to the
# KMS-encrypted log group.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_stage" "main" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = var.stage_name
  auto_deploy = true

  default_route_settings {
    detailed_metrics_enabled = true
    throttling_burst_limit   = var.throttling_burst_limit
    throttling_rate_limit    = var.throttling_rate_limit
  }

  # Per-route throttling for explicit override routes that carry a
  # `throttle` block in `local.explicit_overrides`. The dynamic block
  # iterates the SAME map the route resources iterate, so adding a new
  # throttled override is a single map-entry edit and the stage picks
  # it up automatically. Routes without a throttle block (or routes
  # not in the override map at all) inherit `default_route_settings`
  # above. Per ADR-038 this is the per-route policy hook for the
  # (c+) routing shape.
  dynamic "route_settings" {
    for_each = {
      for route_key, override in local.active_overrides :
      route_key => override
      if override.throttle != null
    }

    content {
      route_key                = aws_apigatewayv2_route.service_override[route_settings.key].route_key
      detailed_metrics_enabled = true
      throttling_burst_limit   = route_settings.value.throttle.burst_limit
      throttling_rate_limit    = route_settings.value.throttle.rate_limit
    }
  }

  # Access log format
  #
  # The JSON record captures one row per request and is the primary
  # diagnostic surface for gateway-edge failures (5xx, integration
  # timeouts, auth rejections, throttle hits). Fields are grouped:
  #
  #   - Request identity (requestId, requestTime, sourceIp, userAgent)
  #   - Route (httpMethod, path, routeKey). `path` is the raw
  #     forwarded path the gateway saw, `routeKey` is which route
  #     matched. The pair lets us tell a 503 from a missing route
  #     apart from a 503 from a hung upstream. Note: HTTP API v2
  #     does NOT expose `$context.rawQueryString` in access log
  #     variables (the API rejects it with BadRequestException);
  #     query strings are only retrievable via header capture or
  #     integration-side logging.
  #   - Response (status, protocol, responseLength).
  #   - Integration (integrationRequestId, integrationStatus,
  #     integrationLatency, integrationErrorMessage). These four
  #     fields distinguish "upstream returned 5xx" from
  #     "integration timed out at 29s" from "VPC Link refused".
  #     `integrationLatency = 29000` with `status = 503` is the
  #     canonical timeout signature.
  #   - Gateway error (errorMessage, errorResponseType). Populated
  #     when API Gateway itself rejects a request (auth failure,
  #     throttling, integration failure). Empty when the upstream
  #     handled the request cleanly.
  #   - Authorizer (authorizerError, authorizerLatency). Only
  #     unused (authorization_type = NONE on every route) but
  #     forward-wired so the first JWT-authorizer addition surfaces
  #     in logs without a follow-up Terraform diff.
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId               = "$context.requestId"
      requestTime             = "$context.requestTime"
      httpMethod              = "$context.httpMethod"
      path                    = "$context.path"
      routeKey                = "$context.routeKey"
      status                  = "$context.status"
      protocol                = "$context.protocol"
      responseLength          = "$context.responseLength"
      sourceIp                = "$context.identity.sourceIp"
      userAgent               = "$context.identity.userAgent"
      integrationRequestId    = "$context.integration.requestId"
      integrationStatus       = "$context.integrationStatus"
      integrationLatency      = "$context.integrationLatency"
      integrationErrorMessage = "$context.integrationErrorMessage"
      errorMessage            = "$context.error.message"
      errorResponseType       = "$context.error.responseType"
      authorizerError         = "$context.authorizer.error"
      authorizerLatency       = "$context.authorizer.latency"
    })
  }

  tags = local.common_tags
}

# WAF v2 association intentionally NOT defined here.
#
# AWS WAF v2 cannot be associated with API Gateway v2 (HTTP API)
# stages. AWS WAF v2's `AssociateWebACL` accepts CloudFront, ALB,
# REST API (v1) stages, AppSync GraphQL APIs, and Cognito User
# Pools. HTTP API v2 stages are NOT in the supported set; the API
# rejects the association with a `WAFInvalidParameterException`.
#
# Canonical workaround: front the HTTP API with CloudFront and put
# WAF on the CloudFront distribution. CloudFront supports WAF v2
# and adds caching, edge TLS termination, and DDoS shielding as
# bonus benefits. The `dev/frontend/` module already provisions
# CloudFront for the SvelteKit admin; an api-fronting CloudFront
# distribution would follow the same pattern and let WAF cover
# both surfaces.

# ---------------------------------------------------------------------------
# Custom domain placeholder (deferred)
#
# Enabling a custom domain (`api.panakoes.com`) requires:
#   1. An ACM certificate covering the domain in this region.
#   2. A Route 53 (or external DNS) A/AAAA record pointing at the
#      domain name's regional endpoint.
#
# Neither dependency exists yet; the skeleton is left as a
# commented-out block so the future PR is a small, focused diff.
#
# resource "aws_apigatewayv2_domain_name" "main" {
#   count       = var.custom_domain_name == "" ? 0 : 1
#   domain_name = var.custom_domain_name
#
#   domain_name_configuration {
#     certificate_arn = var.custom_domain_certificate_arn
#     endpoint_type   = "REGIONAL"
#     security_policy = "TLS_1_2"
#   }
#
#   tags = local.common_tags
# }
#
# resource "aws_apigatewayv2_api_mapping" "main" {
#   count       = var.custom_domain_name == "" ? 0 : 1
#   api_id      = aws_apigatewayv2_api.main.id
#   domain_name = aws_apigatewayv2_domain_name.main[0].id
#   stage       = aws_apigatewayv2_stage.main.id
# }
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# CloudWatch alarms
#
# Three alarms cover the standard front-door SLOs:
#   - 4xx rate over 10 minutes: client misbehavior or contract drift.
#     Threshold 10% is a "look at it" signal, not a page.
#   - 5xx rate over 5 minutes: API itself failing. Threshold 1%
#     reflects that 5xx should be rare; sustained breach is paging.
#   - Integration latency p99 over 2s: upstream slowing. Threshold
#     2000 ms is the user-perceived-slow line for a synchronous API.
#
# Both error-rate alarms use a metric math expression dividing the
# count metric by the total Count metric, multiplied by 100 to express
# percent. CloudWatch's percentile statistic on IntegrationLatency
# gives us p99 directly without manual aggregation.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "client_error_rate" {
  alarm_name        = "${local.name_prefix}-api-gateway-4xx-rate"
  alarm_description = "API Gateway 4xx rate exceeds ${var.alarm_4xx_threshold_percent}% over 10 minutes (sustained client errors; investigate)."

  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 10
  threshold           = var.alarm_4xx_threshold_percent
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "rate"
    expression  = "(client_errors / total_requests) * 100"
    label       = "4xx percent"
    return_data = true
  }

  metric_query {
    id = "client_errors"

    metric {
      metric_name = "4xx"
      namespace   = "AWS/ApiGateway"
      period      = var.alarm_period_seconds
      stat        = "Sum"

      dimensions = {
        ApiId = aws_apigatewayv2_api.main.id
        Stage = aws_apigatewayv2_stage.main.name
      }
    }
  }

  metric_query {
    id = "total_requests"

    metric {
      metric_name = "Count"
      namespace   = "AWS/ApiGateway"
      period      = var.alarm_period_seconds
      stat        = "Sum"

      dimensions = {
        ApiId = aws_apigatewayv2_api.main.id
        Stage = aws_apigatewayv2_stage.main.name
      }
    }
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "server_error_rate" {
  alarm_name        = "${local.name_prefix}-api-gateway-5xx-rate"
  alarm_description = "API Gateway 5xx rate exceeds ${var.alarm_5xx_threshold_percent}% over 5 minutes (gateway / upstream failing; page on-call)."

  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  threshold           = var.alarm_5xx_threshold_percent
  treat_missing_data  = "notBreaching"

  metric_query {
    id          = "rate"
    expression  = "(server_errors / total_requests) * 100"
    label       = "5xx percent"
    return_data = true
  }

  metric_query {
    id = "server_errors"

    metric {
      metric_name = "5xx"
      namespace   = "AWS/ApiGateway"
      period      = var.alarm_period_seconds
      stat        = "Sum"

      dimensions = {
        ApiId = aws_apigatewayv2_api.main.id
        Stage = aws_apigatewayv2_stage.main.name
      }
    }
  }

  metric_query {
    id = "total_requests"

    metric {
      metric_name = "Count"
      namespace   = "AWS/ApiGateway"
      period      = var.alarm_period_seconds
      stat        = "Sum"

      dimensions = {
        ApiId = aws_apigatewayv2_api.main.id
        Stage = aws_apigatewayv2_stage.main.name
      }
    }
  }

  tags = local.common_tags
}

resource "aws_cloudwatch_metric_alarm" "integration_latency_p99" {
  alarm_name        = "${local.name_prefix}-api-gateway-integration-latency-p99"
  alarm_description = "API Gateway integration latency p99 exceeds ${var.alarm_p99_latency_ms} ms (upstream service slowing)."

  metric_name         = "IntegrationLatency"
  namespace           = "AWS/ApiGateway"
  comparison_operator = "GreaterThanThreshold"
  evaluation_periods  = 5
  period              = var.alarm_period_seconds
  threshold           = var.alarm_p99_latency_ms
  extended_statistic  = "p99"
  treat_missing_data  = "notBreaching"

  dimensions = {
    ApiId = aws_apigatewayv2_api.main.id
    Stage = aws_apigatewayv2_stage.main.name
  }

  tags = local.common_tags
}

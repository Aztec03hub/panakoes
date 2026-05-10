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
  # Service NLB listener ARNs (services-first incremental rollout)
  #
  # API Gateway HTTP API VPC Link integrations require a real NLB
  # listener ARN as the integration target; the API Gateway service
  # validates the target on integration create and rejects placeholder
  # ARNs with `BadRequestException: Invalid VPC Link target` (this is
  # what blocked the 2026-05-09 first apply).
  #
  # Pattern: pull the map from the ECS module's remote state. Until
  # that module ships (or for services within it that are not yet
  # deployed), the `try()` returns an empty map and the integrations +
  # routes for_each below produce zero resources. Each service's
  # integration + route appear automatically on the next apply once
  # its NLB listener ARN lands in the ECS module's `nlb_listener_arns`
  # output.
  # ---------------------------------------------------------------------
  service_nlb_listener_arns = (
    var.discover_ecs_nlbs && length(data.terraform_remote_state.ecs) > 0
    ? try(data.terraform_remote_state.ecs[0].outputs.nlb_listener_arns, {})
    : {}
  )

  # ---------------------------------------------------------------------
  # Route table
  #
  # Each entry maps a `METHOD /path` route key to the upstream service
  # whose VPC Link integration handles it. Driven through `for_each`
  # to keep the route surface declarative and easy to audit at a
  # glance.
  #
  # The `auth` flag flips authorization off for the unauthenticated
  # routes (Stripe webhook, public health check). The HTTP API does
  # not yet attach a JWT authorizer at the gateway layer; per-service
  # JWT validation already lives in the auth, ingestion-api, and
  # query-api services (see services/*/auth.py). When we promote
  # auth to the gateway layer, this `auth` field becomes the input to
  # an `aws_apigatewayv2_authorizer` lookup.
  # ---------------------------------------------------------------------
  routes = {
    # Auth service
    "POST /auth/sign-up"  = { service = "auth", auth = false }
    "POST /auth/sign-in"  = { service = "auth", auth = false }
    "POST /auth/sign-out" = { service = "auth", auth = true }
    "POST /auth/validate" = { service = "auth", auth = false }

    # Ingestion API
    "POST /ingestion/audio" = { service = "ingestion-api", auth = true }
    "GET /ingestion/{id}"   = { service = "ingestion-api", auth = true }
    "GET /ingestion"        = { service = "ingestion-api", auth = true }

    # Summarization
    "POST /summarize"   = { service = "summarization", auth = true }
    "GET /summary/{id}" = { service = "summarization", auth = true }
    "GET /summaries"    = { service = "summarization", auth = true }

    # Notification
    "POST /notify/email"      = { service = "notification", auth = true }
    "POST /notify/webhook"    = { service = "notification", auth = true }
    "GET /notifications"      = { service = "notification", auth = true }
    "GET /notifications/{id}" = { service = "notification", auth = true }

    # Query API
    "GET /transcripts"      = { service = "query-api", auth = true }
    "GET /transcripts/{id}" = { service = "query-api", auth = true }
    "GET /sessions"         = { service = "query-api", auth = true }
    "GET /sessions/{id}"    = { service = "query-api", auth = true }

    # Session manager (write-side of /sessions)
    "POST /sessions"        = { service = "session-manager", auth = true }
    "PATCH /sessions/{id}"  = { service = "session-manager", auth = true }
    "DELETE /sessions/{id}" = { service = "session-manager", auth = true }

    # Billing
    "POST /billing/checkout-session" = { service = "billing", auth = true }
    "POST /billing/portal"           = { service = "billing", auth = true }
    "GET /billing/subscription"      = { service = "billing", auth = true }
    # Stripe webhooks must be reachable without a user JWT; Stripe
    # signs the request body and the billing service validates the
    # signature with the `stripe_webhook_signing` secret. Treat the
    # webhook as anonymous at the gateway layer and let the service
    # do its own auth.
    "POST /billing/webhook" = { service = "billing", auth = false }
  }

  # ---------------------------------------------------------------------
  # Active routes: filtered to services whose NLB listener ARN has been
  # discovered via remote state. A service that has not yet deployed
  # an NLB simply does not appear in `service_nlb_listener_arns`, and
  # its routes drop out of the apply. The next apply after that
  # service ships its listener ARN materializes the corresponding
  # integration + routes automatically, no hand-edit required.
  # ---------------------------------------------------------------------
  active_routes = {
    for route_key, route in local.routes :
    route_key => route
    if contains(keys(local.service_nlb_listener_arns), route.service)
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
# Per-service VPC Link integrations
#
# One integration per upstream service whose NLB listener ARN has
# been discovered via the ECS module's remote state (see
# `local.service_nlb_listener_arns` above). When zero services have
# shipped, this resource produces zero integrations; each service's
# integration appears automatically on the next apply once its
# listener ARN is exported.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_integration" "service" {
  for_each = local.service_nlb_listener_arns

  api_id             = aws_apigatewayv2_api.main.id
  integration_type   = "HTTP_PROXY"
  integration_method = "ANY"
  connection_type    = "VPC_LINK"
  connection_id      = aws_apigatewayv2_vpc_link.main.id
  integration_uri    = each.value

  payload_format_version = "1.0"
  timeout_milliseconds   = 29000

  # No path mapping. Forward the request path unchanged to the backend.
  #
  # Why: PR #206 added `overwrite:path = /$request.path.proxy`, which
  # required every route to use the `{proxy+}` greedy-capture path
  # parameter shape (e.g. `/v1/{service}/{proxy+}`). The existing route
  # table uses literal paths (`POST /auth/sign-up` etc.) with no proxy
  # capture, so `$request.path.proxy` resolved to empty and the
  # gateway forwarded `/` to the backend, breaking every existing
  # route. Reverted in PR (this) so the literal route table works.
  #
  # If proxy-shaped routes are added later, layer the path mapping
  # back via a per-integration request_parameters override OR adopt
  # the proxy-route simplification fully (deferred decision; see
  # `panakoes_api_gateway_proxy_route_simplification_deferred.md`).
}

# ---------------------------------------------------------------------------
# Routes
#
# Built from the `local.routes` map. Each route forwards to the
# matching service's integration. `authorization_type = "NONE"` for
# every route today; when we promote auth to the gateway layer the
# `auth = true` rows will pivot to `JWT` plus the authorizer ID.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_route" "service" {
  for_each = local.active_routes

  api_id    = aws_apigatewayv2_api.main.id
  route_key = each.key
  target    = "integrations/${aws_apigatewayv2_integration.service[each.value.service].id}"

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

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId               = "$context.requestId"
      requestTime             = "$context.requestTime"
      httpMethod              = "$context.httpMethod"
      routeKey                = "$context.routeKey"
      status                  = "$context.status"
      protocol                = "$context.protocol"
      responseLength          = "$context.responseLength"
      sourceIp                = "$context.identity.sourceIp"
      userAgent               = "$context.identity.userAgent"
      integrationStatus       = "$context.integrationStatus"
      integrationLatency      = "$context.integrationLatency"
      integrationErrorMessage = "$context.integrationErrorMessage"
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

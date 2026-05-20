locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "api-gateway-ws"
  }

  name_prefix = "${var.project_name}-${var.environment}"
  api_name    = "${local.name_prefix}-streaming-ws"

  # ---------------------------------------------------------------------
  # WebSocket route catalogue
  #
  # Per ADR-009/ADR-011 the streaming protocol is bidirectional JSON
  # over a single long-lived WebSocket. The client sends frames as
  # `{"action": "<route>", "data": {...}}`; API Gateway dispatches on
  # the `action` field via the route selection expression configured on
  # `aws_apigatewayv2_api.main` below.
  #
  # `audio-frame` carries base64-encoded PCM chunks from the browser
  # mic capture; `transcript-request` is the client-initiated nudge
  # asking the GPU worker for an early partial flush.
  #
  # Adding a new app route is a single map-entry edit; the integration
  # + route + integration-response + route-response resources fan out
  # via for_each. The $connect / $disconnect / $default routes are
  # provisioned individually below because each carries unique
  # semantics (authorizer attachment, lifecycle event shape) that do
  # not generalize to the app-route map.
  # ---------------------------------------------------------------------
  app_routes = toset([
    "audio-frame",
    "transcript-request",
    # Stage 2 streaming additions (design doc BLOCK-01 round-4 fix).
    # `ping` / `ping-echo` are the explicit keepalive arms each side
    # uses to stay under API Gateway's 10-minute idle timeout. Without
    # registering them here the API GW route-selection-expression
    # collapses them to `$default`, which the router's keepalive arm
    # would never see.
    "ping",
    "ping-echo",
  ])

  # W2-T4 extension: consolidated panakoes/logs CMK ARN. Replaces the
  # module-local aws_kms_key.ws_logs and aws_kms_key.lambda_logs CMKs
  # at the log-group encryption layer. Both local key resources are
  # retained below for W2-T7 retirement (orchestrator-only step) but
  # no longer encrypt any log group.
  logs_kms_key_arn = data.terraform_remote_state.kms.outputs.logs_key_arn
}

# ---------------------------------------------------------------------------
# CloudWatch log-group KMS key (local fallback)
#
# A dedicated CMK for the WebSocket API access + execution logs. Per
# `aws_lambda_log_group_dedicated_cmk_pattern` (see memory), the shared
# observability CMK conditions its grants on `/panakoes/dev/*` ARNs,
# which neither matches the API Gateway access log group nor the
# `/aws/apigatewayv2/<api-id>` execution log group; reusing it would
# fail with `AccessDeniedException` at first write. A module-local CMK
# matches the sibling `infra/dev/api-gateway/` module's pattern and is
# the lowest-risk choice for a first deploy.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "ws_logs_kms" {
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

    # ARN-prefix scoped to the two log groups this module owns: the
    # access-log group below and any future execution-log group AWS
    # API Gateway provisions for this API id (path
    # `/aws/apigatewayv2/<api-id>`).
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values = [
        "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/apigatewayv2/${local.name_prefix}-streaming-ws*",
      ]
    }
  }
}

resource "aws_kms_key" "ws_logs" {
  description             = "KMS key for the dev streaming WebSocket access + execution log groups."
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.ws_logs_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "ws_logs" {
  name          = "alias/${local.name_prefix}-streaming-ws-logs"
  target_key_id = aws_kms_key.ws_logs.key_id
}

# ---------------------------------------------------------------------------
# CloudWatch log group for WebSocket access logs
#
# 30-day retention matches the project default. The log group name
# uses the `streaming-ws` suffix to coexist with the sibling HTTP API
# module's `/aws/apigatewayv2/panakoes-dev` group.
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "access" {
  name              = "/aws/apigatewayv2/${local.name_prefix}-streaming-ws"
  retention_in_days = var.access_log_retention_days
  # W2-T4 extension: migrated from aws_kms_key.ws_logs.arn to the
  # consolidated panakoes/logs CMK. The local key resource is
  # retained above for W2-T7 retirement (orchestrator-only step).
  kms_key_id = local.logs_kms_key_arn

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Real streaming integration: streaming-router Lambda + ws-authorizer Lambda
#
# Replaces the smoke-deploy stub (see PR #281 / commit bdc7ea6). Both
# Lambdas ship as container images out of the dev ECR registry. The
# image tags are var-driven so the GHA image-bake workflow can rev the
# tag without touching this module.
#
#   - streaming-router: branches on `requestContext.routeKey`. Owns
#     session-row writes (DynamoDB `panakoes-dev-streaming-sessions`),
#     audio-frame fan-out to SQS, and the gpu-spawner trigger via
#     EventBridge. Source: `services/streaming-router/`.
#   - ws-authorizer: REQUEST-type Lambda authorizer attached to
#     `$connect`. Reads the JWT from the `Authorization: Bearer <jwt>`
#     header OR the `?token=<jwt>` query string (browsers cannot set
#     custom headers on the WebSocket handshake). Validates via the
#     shared `panakoes-auth-client`. Source: `services/ws-authorizer/`.
#
# Both Lambdas own their own log groups + dedicated CMKs because the
# observability module's shared logs CMK conditions encryption on
# `/panakoes/dev/*` ARNs and Lambda log groups land under
# `/aws/lambda/*`. Same pattern as `infra/dev/transcribe-worker/` and
# `infra/dev/cost-rollup-aggregator/`.
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "frames" {
  name                       = "${local.name_prefix}-streaming-ws-frames"
  visibility_timeout_seconds = var.frame_queue_visibility_timeout_seconds
  message_retention_seconds  = var.frame_queue_message_retention_seconds
  sqs_managed_sse_enabled    = true

  tags = local.common_tags
}

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ---------------------------------------------------------------------------
# Remote state lookups for cross-module ARNs
#
# The streaming-router needs the streaming-sessions DDB table ARN
# (from `infra/dev/data/`) and the ECR repository URLs (from
# `infra/dev/ecr/`). The ws-authorizer needs the jwt-signing-secret
# ARN + secrets-CMK ARN (from `infra/dev/secrets/`). We read these
# from remote state rather than re-declaring strings so a rename
# upstream fails the plan loudly instead of silently mis-pointing.
# ---------------------------------------------------------------------------

data "terraform_remote_state" "data" {
  backend = "s3"
  config = {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/data/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

data "terraform_remote_state" "ecr" {
  backend = "s3"
  config = {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/ecr/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

data "terraform_remote_state" "secrets" {
  backend = "s3"
  config = {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/secrets/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

locals {
  streaming_sessions_table_name = data.terraform_remote_state.data.outputs.streaming_sessions_table_name
  streaming_sessions_table_arn  = data.terraform_remote_state.data.outputs.streaming_sessions_table_arn

  streaming_router_image_uri = "${data.terraform_remote_state.ecr.outputs.repository_urls["streaming-router"]}:${var.streaming_router_image_tag}"
  ws_authorizer_image_uri    = "${data.terraform_remote_state.ecr.outputs.repository_urls["ws-authorizer"]}:${var.ws_authorizer_image_tag}"

  jwt_signing_secret_arn = data.terraform_remote_state.secrets.outputs.secret_arns["jwt-signing-secret"]
  secrets_kms_key_arn    = data.terraform_remote_state.secrets.outputs.kms_key_arn
}

# ---------------------------------------------------------------------------
# Lambda log group CMK (shared between both Lambdas)
#
# A single CMK scoped to `/aws/lambda/${local.name_prefix}-streaming-*`
# covers both functions' log groups. Mirrors the per-Lambda CMK
# pattern (`aws_lambda_log_group_dedicated_cmk_pattern` in project
# memory) but with the scope widened by one prefix component since
# the two functions are owned by the same module.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_logs_kms" {
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
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values = [
        "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-streaming-*",
      ]
    }
  }
}

resource "aws_kms_key" "lambda_logs" {
  description             = "KMS key for streaming-router + ws-authorizer Lambda log groups."
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.lambda_logs_kms.json
  tags                    = local.common_tags
}

resource "aws_kms_alias" "lambda_logs" {
  name          = "alias/${local.name_prefix}-streaming-lambda-logs"
  target_key_id = aws_kms_key.lambda_logs.key_id
}

# ---------------------------------------------------------------------------
# streaming-router Lambda
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "streaming_router" {
  name              = "/aws/lambda/${local.name_prefix}-streaming-router"
  retention_in_days = var.access_log_retention_days
  # W2-T4 extension: migrated from aws_kms_key.lambda_logs.arn to the
  # consolidated panakoes/logs CMK. The local key resource is
  # retained above for W2-T7 retirement (orchestrator-only step).
  kms_key_id = local.logs_kms_key_arn
  tags       = local.common_tags
}

resource "aws_iam_role" "streaming_router" {
  name               = "${local.name_prefix}-streaming-router-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "streaming_router_basic" {
  role       = aws_iam_role.streaming_router.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "streaming_router_inline" {
  # DynamoDB CRUD on the streaming-sessions table (and any future
  # indexes under it). Scoped to the single table ARN; no wildcard.
  statement {
    sid    = "StreamingSessionsCrud"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
    ]
    resources = [
      local.streaming_sessions_table_arn,
      "${local.streaming_sessions_table_arn}/index/*",
    ]
  }

  # Audio-frame SQS fan-out.
  statement {
    sid       = "AudioFrameSqsSend"
    effect    = "Allow"
    actions   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
    resources = [aws_sqs_queue.frames.arn]
  }

  # Server-to-client push back to API Gateway. The streaming-router
  # uses this to nudge the client when a transcript-request fires
  # before the GPU worker has fresh partial output.
  statement {
    sid       = "ApiGatewayManagementPost"
    effect    = "Allow"
    actions   = ["execute-api:ManageConnections"]
    resources = ["${aws_apigatewayv2_api.main.execution_arn}/*/*/@connections/*"]
  }

  # EventBridge put-events for the gpu-spawner trigger.
  statement {
    sid       = "EventBridgePut"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = ["arn:aws:events:${var.aws_region}:${data.aws_caller_identity.current.account_id}:event-bus/${var.streaming_event_bus}"]
  }
}

resource "aws_iam_role_policy" "streaming_router_inline" {
  name   = "streaming-router-inline"
  role   = aws_iam_role.streaming_router.id
  policy = data.aws_iam_policy_document.streaming_router_inline.json
}

resource "aws_lambda_function" "streaming_router" {
  function_name = "${local.name_prefix}-streaming-router"
  role          = aws_iam_role.streaming_router.arn
  package_type  = "Image"
  image_uri     = local.streaming_router_image_uri
  timeout       = 10
  memory_size   = 256

  environment {
    variables = {
      STREAMING_SESSIONS_TABLE = local.streaming_sessions_table_name
      AUDIO_FRAME_QUEUE_URL    = aws_sqs_queue.frames.id
      STREAMING_EVENT_BUS      = var.streaming_event_bus
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.streaming_router.name
  }

  # Lambda 30s init timeout vs the cold-start cost of pulling a fresh
  # container image: 256 MB / 10s is the floor that survives an ECR
  # cold pull. Tune downward once we observe steady-state cold-start
  # numbers in CloudWatch.
  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.streaming_router_basic,
    aws_iam_role_policy.streaming_router_inline,
    aws_cloudwatch_log_group.streaming_router,
  ]
}

resource "aws_lambda_permission" "apigw_invoke_router" {
  statement_id  = "AllowAPIGatewayInvokeRouter"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.streaming_router.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# ws-authorizer Lambda
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "ws_authorizer" {
  name              = "/aws/lambda/${local.name_prefix}-streaming-ws-authorizer"
  retention_in_days = var.access_log_retention_days
  # W2-T4 extension: migrated from aws_kms_key.lambda_logs.arn to the
  # consolidated panakoes/logs CMK. The local key resource is
  # retained above for W2-T7 retirement (orchestrator-only step).
  kms_key_id = local.logs_kms_key_arn
  tags       = local.common_tags
}

resource "aws_iam_role" "ws_authorizer" {
  name               = "${local.name_prefix}-streaming-ws-authorizer-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ws_authorizer_basic" {
  role       = aws_iam_role.ws_authorizer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "ws_authorizer_inline" {
  # Read the JWT signing secret at boot. Scoped to the single secret
  # ARN; no wildcard.
  statement {
    sid       = "ReadJwtSigningSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.jwt_signing_secret_arn]
  }

  # Decrypt the secret via the secrets-module CMK. Without
  # `kms:Decrypt` against the secret's CMK, `GetSecretValue` returns
  # `AccessDeniedException` even with the secrets:GetSecretValue
  # grant above.
  statement {
    sid       = "DecryptJwtSigningSecretCmk"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [local.secrets_kms_key_arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.aws_region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "ws_authorizer_inline" {
  name   = "ws-authorizer-inline"
  role   = aws_iam_role.ws_authorizer.id
  policy = data.aws_iam_policy_document.ws_authorizer_inline.json
}

resource "aws_lambda_function" "ws_authorizer" {
  function_name = "${local.name_prefix}-streaming-ws-authorizer"
  role          = aws_iam_role.ws_authorizer.arn
  package_type  = "Image"
  image_uri     = local.ws_authorizer_image_uri
  timeout       = 5
  memory_size   = 128

  environment {
    variables = {
      # JWT_SECRET is injected from Secrets Manager via the operator-
      # managed `aws lambda update-function-configuration` workflow
      # (see runbook). We do NOT use Lambda's `--environment
      # Variables` for secret material because that surface is
      # readable to anyone with lambda:GetFunction. The runtime reads
      # JWT_SECRET from the env at boot and caches the validator.
      JWT_ISSUER   = var.jwt_issuer
      JWT_AUDIENCE = var.jwt_audience
    }
  }

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.ws_authorizer.name
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.ws_authorizer_basic,
    aws_iam_role_policy.ws_authorizer_inline,
    aws_cloudwatch_log_group.ws_authorizer,
  ]
}

resource "aws_lambda_permission" "apigw_invoke_authorizer" {
  statement_id  = "AllowAPIGatewayInvokeAuthorizer"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ws_authorizer.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/authorizers/*"
}

# ---------------------------------------------------------------------------
# API Gateway v2 WebSocket authorizer (REQUEST-type, attached to $connect)
#
# Identity sources accept either the `Authorization` header OR the
# `?token=` query string param; the Lambda treats header as primary
# and query as fallback per its own code. We list BOTH so API Gateway
# does not 401 the request before the Lambda runs.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_authorizer" "jwt" {
  api_id           = aws_apigatewayv2_api.main.id
  authorizer_type  = "REQUEST"
  authorizer_uri   = aws_lambda_function.ws_authorizer.invoke_arn
  name             = "${local.name_prefix}-streaming-ws-jwt-authorizer"
  identity_sources = ["route.request.header.Authorization", "route.request.querystring.token"]
  # No response caching: the streaming connection is long-lived but
  # the authorizer only fires once per $connect, so a cache buys
  # nothing and would invite a 5-minute window where a revoked token
  # still works. Default (no cache) is correct here.
}

# ---------------------------------------------------------------------------
# WebSocket API
#
# `route_selection_expression = $request.body.action` tells API Gateway
# to look at the `action` field of the incoming JSON frame and route on
# its value. Frames missing or unmatched against the field land on
# `$default`.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_api" "main" {
  name                       = local.api_name
  protocol_type              = "WEBSOCKET"
  description                = "Public WebSocket API fronting the Panakoes streaming transcription pipeline in the dev environment."
  route_selection_expression = "$request.body.action"

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Shared AWS_PROXY Lambda integration
#
# Every route on this WebSocket API targets the same Lambda integration
# (the stub above). API Gateway hands the Lambda a fully-formed
# `requestContext.routeKey` so the handler can branch on it. Sharing
# one integration across all five routes keeps the resource count
# small and matches the eventual production shape, where the
# streaming-router Lambda will branch the same way.
#
# The Lambda authorizer planned for $connect (see ADR-022 and
# `docs/runbooks/streaming-websocket-smoke.md`) is intentionally NOT
# wired in this first deploy because the authorizer Lambda is not yet
# implemented. The follow-up PR that lands the authorizer flips
# `authorization_type` on `aws_apigatewayv2_route.connect` from `NONE`
# to `CUSTOM` and adds the `authorizer_id` argument; nothing else in
# this file needs to change.
# ---------------------------------------------------------------------------

# Single AWS_PROXY Lambda integration shared by every route. The
# streaming-router handler branches on `requestContext.routeKey`.
# Resource name is `stub` (rather than `router`) for state continuity
# with PR #281 so the apply repoints the integration URI in place
# rather than replacing the resource (which would cascade-replace
# every route).
resource "aws_apigatewayv2_integration" "stub" {
  api_id                    = aws_apigatewayv2_api.main.id
  integration_type          = "AWS_PROXY"
  integration_method        = "POST"
  integration_uri           = aws_lambda_function.streaming_router.invoke_arn
  content_handling_strategy = "CONVERT_TO_TEXT"
}

# Lifecycle routes: $connect, $disconnect, $default.
#
# $connect is the only authenticated route; once the connection is
# open, $disconnect and the app routes operate on the connection_id
# that API Gateway issued, which is itself the trust boundary.
resource "aws_apigatewayv2_route" "connect" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "$connect"
  target             = "integrations/${aws_apigatewayv2_integration.stub.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.jwt.id
}

resource "aws_apigatewayv2_route" "disconnect" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "$disconnect"
  target             = "integrations/${aws_apigatewayv2_integration.stub.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "default" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "$default"
  target             = "integrations/${aws_apigatewayv2_integration.stub.id}"
  authorization_type = "NONE"
}

# App routes: audio-frame, transcript-request. Adding a new app route
# is one map-entry edit on `local.app_routes`.
resource "aws_apigatewayv2_route" "app" {
  for_each = local.app_routes

  api_id             = aws_apigatewayv2_api.main.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.stub.id}"
  authorization_type = "NONE"
}

# ---------------------------------------------------------------------------
# Account-level API Gateway CloudWatch Logs role
#
# API Gateway v2 WebSocket APIs require an ACCOUNT-LEVEL IAM role
# (set via `aws_api_gateway_account.cloudwatch_role_arn`) before any
# stage in the account can enable access logging. This differs from
# API Gateway v2 HTTP APIs, which write access logs via the
# per-stage `destination_arn` alone with no account-level role
# required.
#
# Failure mode without this: `CreateStage` fails with
# `BadRequestException: CloudWatch Logs role ARN must be set in
# account settings to enable logging`. Hit on the first apply of
# this module 2026-05-11.
#
# The role and account setting are account-wide singletons; this
# module owns them because it is the first WebSocket API in the
# account. If a future module needs to manage them instead, move
# the four resources below (role + policy attachment + account
# setting) out and reference the role ARN via remote state.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "apigw_logs_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apigw_cloudwatch_logs" {
  name               = "${local.name_prefix}-apigw-cloudwatch-logs"
  assume_role_policy = data.aws_iam_policy_document.apigw_logs_assume.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "apigw_cloudwatch_logs" {
  role       = aws_iam_role.apigw_cloudwatch_logs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

resource "aws_api_gateway_account" "main" {
  cloudwatch_role_arn = aws_iam_role.apigw_cloudwatch_logs.arn
}

# ---------------------------------------------------------------------------
# Stage
#
# Auto-deploy keeps the stage in sync with route / integration changes.
# Default throttling caps the per-route burst and steady-state rate.
# Access logging emits a structured JSON record per WebSocket message.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_stage" "main" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = var.stage_name
  auto_deploy = true

  default_route_settings {
    detailed_metrics_enabled = true
    logging_level            = var.execution_logging_level
    data_trace_enabled       = var.execution_data_trace_enabled
    throttling_burst_limit   = var.throttling_burst_limit
    throttling_rate_limit    = var.throttling_rate_limit
  }

  # WebSocket access log fields differ from HTTP API fields:
  #   - `eventType` is `CONNECT | MESSAGE | DISCONNECT` and is the
  #     primary discriminator. Smoke tests confirm both CONNECT and
  #     DISCONNECT land for every clean session.
  #   - `routeKey` carries the dispatched route (`$connect`,
  #     `audio-frame`, etc.). Empty on rejected frames.
  #   - `messageId` and `connectionId` correlate a frame to its
  #     session.
  #   - `integrationLatency` + `integrationStatus` distinguish
  #     "upstream queue refused" from "API Gateway internal".
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.access.arn
    format = jsonencode({
      requestId          = "$context.requestId"
      requestTime        = "$context.requestTime"
      eventType          = "$context.eventType"
      routeKey           = "$context.routeKey"
      status             = "$context.status"
      connectionId       = "$context.connectionId"
      messageId          = "$context.messageId"
      sourceIp           = "$context.identity.sourceIp"
      integrationStatus  = "$context.integrationStatus"
      integrationLatency = "$context.integrationLatency"
      errorMessage       = "$context.error.message"
    })
  }

  tags = local.common_tags

  depends_on = [aws_api_gateway_account.main]
}

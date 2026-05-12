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
  ])
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
  kms_key_id        = aws_kms_key.ws_logs.arn

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Smoke-deploy integration target: stub Lambda + SQS fanout
#
# API Gateway v2 WebSocket APIs accept `AWS_PROXY` and `AWS` integration
# types but NOT `MOCK` (MOCK is REST-only). The real downstream
# consumer (the streaming-router + Lambda authorizer) is not yet built
# (services/ is intentionally out of scope for the deploy PR), so this
# module ships a tiny inline Python Lambda that:
#
#   1. Logs the route, connection id, and body to CloudWatch (gives
#      operators a single place to confirm a frame reached the
#      gateway and dispatched to the right route).
#   2. Forwards the same record to an SQS queue (the future
#      streaming-router will replace step (1) but may keep the queue
#      as a buffered fan-out, so the queue stays after the cutover).
#   3. Returns `{statusCode: 200, body: '{"ok":true}'}` so the
#      WebSocket connection completes cleanly.
#
# AWS_PROXY Lambda integration is the AWS-documented happy path for
# WebSocket APIs; it sidesteps the VTL request-template gymnastics
# that direct `AWS` SQS integration on WebSocket APIs requires (and
# that broke on the first deploy attempt, see PR description).
#
# When the real router + authorizer land, a follow-up PR repoints the
# integration's `integration_uri` from this stub's ARN to the new
# router Lambda's ARN. No route resource changes, no integration
# resource shape changes.
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

resource "aws_iam_role" "stub_lambda" {
  name               = "${local.name_prefix}-streaming-ws-stub-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "stub_lambda_basic" {
  role       = aws_iam_role.stub_lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "stub_lambda_send_message" {
  statement {
    effect    = "Allow"
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.frames.arn]
  }
}

resource "aws_iam_role_policy" "stub_lambda_send_message" {
  name   = "send-frame-message"
  role   = aws_iam_role.stub_lambda.id
  policy = data.aws_iam_policy_document.stub_lambda_send_message.json
}

# Tiny Python handler. Inlined as a heredoc + archive_file so the
# module has zero external file dependencies; the handler logs the
# incoming event and forwards a structured record to the SQS frame
# queue, then returns a 200 to close the WebSocket loop.
data "archive_file" "stub_lambda" {
  type        = "zip"
  output_path = "${path.module}/stub_lambda.zip"

  source {
    filename = "index.py"
    content  = <<-PYTHON
      import json
      import os
      import boto3

      sqs = boto3.client("sqs")
      QUEUE_URL = os.environ["FRAME_QUEUE_URL"]


      def handler(event, _context):
          ctx = event.get("requestContext", {})
          route = ctx.get("routeKey", "?")
          connection_id = ctx.get("connectionId", "?")
          event_type = ctx.get("eventType", "?")
          body = event.get("body")

          record = {
              "route": route,
              "connectionId": connection_id,
              "eventType": event_type,
              "body": body,
          }
          print("streaming-ws-stub: " + json.dumps(record))

          sqs.send_message(
              QueueUrl=QUEUE_URL,
              MessageBody=json.dumps(record),
          )

          return {
              "statusCode": 200,
              "body": json.dumps({"ok": True, "route": route}),
          }
    PYTHON
  }
}

resource "aws_lambda_function" "stub" {
  function_name    = "${local.name_prefix}-streaming-ws-stub"
  role             = aws_iam_role.stub_lambda.arn
  handler          = "index.handler"
  runtime          = "python3.12"
  filename         = data.archive_file.stub_lambda.output_path
  source_code_hash = data.archive_file.stub_lambda.output_base64sha256
  timeout          = 5

  environment {
    variables = {
      FRAME_QUEUE_URL = aws_sqs_queue.frames.id
    }
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.stub_lambda_basic,
    aws_iam_role_policy.stub_lambda_send_message,
  ]
}

resource "aws_lambda_permission" "apigw_invoke" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.stub.function_name
  principal     = "apigateway.amazonaws.com"
  # `*/*/*` covers every stage + route + method on this WS API id.
  source_arn = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
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

resource "aws_apigatewayv2_integration" "stub" {
  api_id                    = aws_apigatewayv2_api.main.id
  integration_type          = "AWS_PROXY"
  integration_method        = "POST"
  integration_uri           = aws_lambda_function.stub.invoke_arn
  content_handling_strategy = "CONVERT_TO_TEXT"
}

# Lifecycle routes: $connect, $disconnect, $default.
resource "aws_apigatewayv2_route" "connect" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "$connect"
  target    = "integrations/${aws_apigatewayv2_integration.stub.id}"

  # Authorizer attaches in the follow-up PR that ships the Lambda
  # authorizer. The smoke deploy validates the route + integration +
  # downstream Lambda handoff without a JWT gate; ADR-022 + the
  # runbook document the locked JWT shape (HS256, iss
  # `https://auth.panakoes.com`, aud `panakoes-api`, sub = user id)
  # that the authorizer will validate via the `panakoes-auth-client`
  # Lambda layer.
  authorization_type = "NONE"
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

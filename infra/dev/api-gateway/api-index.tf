# ---------------------------------------------------------------------------
# api-index Lambda: root landing + health + friendly 404
#
# The catch-all `ANY /v1/<service>/{proxy+}` routes in main.tf leave the
# root path uncovered, so a bare `GET /` returned API Gateway's stock
# `{"message":"Not Found"}`. This file wires a tiny container-image
# Lambda (`services/api-index/`) behind three routes so the public API
# has a polished front door:
#
#   - GET /        : content-negotiated index (HTML for browsers, JSON
#                    otherwise).
#   - GET /health  : cheap liveness for the index service itself.
#   - $default     : friendly 404 for any unmatched path (the catch-all
#                    proxy routes are MORE specific, so they still win
#                    for `/v1/<service>/...`; `$default` only catches
#                    paths no other route matched, e.g. the root-level
#                    typo `/helth` or `/favicon.ico`).
#
# No `$default` route existed in this module before this file, so adding
# one cannot shadow any existing behavior: the per-service proxy routes
# are explicit and always more specific than `$default`.
#
# IAM is logs-only (least privilege): the handler is pure and makes no
# AWS calls. The log group uses a dedicated module-local CMK because the
# consolidated `panakoes/logs` CMK conditions encryption on
# `/panakoes/dev/*` ARNs and Lambda log groups land under `/aws/lambda/*`
# (same rationale as the sibling Lambda modules).
# ---------------------------------------------------------------------------

locals {
  api_index_function_name = "${local.name_prefix}-api-index"
  api_index_log_group     = "/aws/lambda/${local.name_prefix}-api-index"
  api_index_image_uri     = "${data.terraform_remote_state.ecr.outputs.repository_urls["api-index"]}:${var.api_index_image_tag}"
}

# ---------------------------------------------------------------------------
# Dedicated CMK for the api-index Lambda log group.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "api_index_log_kms" {
  statement {
    sid     = "EnableRootAccountAdmin"
    effect  = "Allow"
    actions = ["kms:*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy document; `*` resolves to the single owning key
    # (`aws_kms_key.api_index_log`). The key ARN is not addressable at
    # policy-creation time.
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
    # KMS key policy: `*` resolves to this key only; service-principal use
    # is further pinned to this Lambda's log group ARN via EncryptionContext.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:${local.api_index_log_group}"]
    }
  }
}

resource "aws_kms_key" "api_index_log" {
  description             = "KMS key for the dev api-index Lambda log group."
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.api_index_log_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "api_index_log" {
  name          = "alias/${local.name_prefix}-api-index-log"
  target_key_id = aws_kms_key.api_index_log.key_id
}

resource "aws_cloudwatch_log_group" "api_index" {
  name              = local.api_index_log_group
  retention_in_days = var.access_log_retention_days
  kms_key_id        = aws_kms_key.api_index_log.arn

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Execution role: logs-only.
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "api_index_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "api_index" {
  name               = "${local.name_prefix}-api-index-lambda"
  assume_role_policy = data.aws_iam_policy_document.api_index_assume.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "api_index_inline" {
  # Write to this function's own log group only. We deliberately do NOT
  # grant `logs:CreateLogGroup`; the group is provisioned above, so the
  # runtime cannot silently re-create an unencrypted group if drift
  # deletes ours.
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      aws_cloudwatch_log_group.api_index.arn,
      "${aws_cloudwatch_log_group.api_index.arn}:*",
    ]
  }
}

resource "aws_iam_role_policy" "api_index_inline" {
  name   = "api-index-inline"
  role   = aws_iam_role.api_index.id
  policy = data.aws_iam_policy_document.api_index_inline.json
}

# ---------------------------------------------------------------------------
# Lambda function.
# ---------------------------------------------------------------------------

resource "aws_lambda_function" "api_index" {
  function_name = local.api_index_function_name
  role          = aws_iam_role.api_index.arn
  package_type  = "Image"
  image_uri     = local.api_index_image_uri
  timeout       = 5
  memory_size   = 128

  logging_config {
    log_format = "JSON"
    log_group  = aws_cloudwatch_log_group.api_index.name
  }

  # The image_uri changes on every push to `:latest`. Ignore drift so a
  # re-apply after a fresh image push does not redundantly try to update
  # the function (matches the cost-rollup-aggregator pattern). Promote to
  # an explicit version-tag scheme via `api_index_image_tag` when adopted.
  lifecycle {
    ignore_changes = [image_uri]
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy.api_index_inline,
    aws_cloudwatch_log_group.api_index,
  ]
}

resource "aws_lambda_permission" "apigw_invoke_api_index" {
  statement_id  = "AllowAPIGatewayInvokeApiIndex"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_index.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.main.execution_arn}/*/*"
}

# ---------------------------------------------------------------------------
# AWS_PROXY integration + routes.
#
# A single integration backs all three routes; the handler branches on
# `requestContext.routeKey`. Payload format 2.0 hands the Lambda the
# `Accept` header for content negotiation.
# ---------------------------------------------------------------------------

resource "aws_apigatewayv2_integration" "api_index" {
  api_id                 = aws_apigatewayv2_api.main.id
  integration_type       = "AWS_PROXY"
  integration_method     = "POST"
  integration_uri        = aws_lambda_function.api_index.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "api_index_root" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "GET /"
  target             = "integrations/${aws_apigatewayv2_integration.api_index.id}"
  authorization_type = "NONE"
}

resource "aws_apigatewayv2_route" "api_index_health" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "GET /health"
  target             = "integrations/${aws_apigatewayv2_integration.api_index.id}"
  authorization_type = "NONE"
}

# $default catches anything no more-specific route matched. The
# per-service `ANY /v1/<service>/{proxy+}` proxy routes are always more
# specific, so this only handles root-level unmatched paths (typos,
# `/favicon.ico`, etc.), returning the handler's friendly 404.
resource "aws_apigatewayv2_route" "api_index_default" {
  api_id             = aws_apigatewayv2_api.main.id
  route_key          = "$default"
  target             = "integrations/${aws_apigatewayv2_integration.api_index.id}"
  authorization_type = "NONE"
}

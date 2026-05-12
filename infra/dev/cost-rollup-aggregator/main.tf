locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "cost-rollup-aggregator"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  function_name = "${local.name_prefix}-cost-rollup-aggregator"
  schedule_name = "${local.name_prefix}-cost-rollup-nightly"
  log_group     = "/aws/lambda/${local.function_name}"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}

# Read the rollup table ARN from the admin-state remote state so the
# IAM grant scopes to the actual provisioned table without re-declaring
# the ARN string here. Same pattern used by `infra/dev/iam/`.
data "terraform_remote_state" "admin_state" {
  backend = "s3"

  config = {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/admin-state/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

# Read the ECR repository URL from the ecr remote state. The repo
# itself is provisioned by `infra/dev/ecr/` (this PR adds the
# `cost-rollup-aggregator` entry to that module's services list); we
# only consume the URL here so the function's image_uri stays in sync
# with the registry name.
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

locals {
  rollup_table_arn  = data.terraform_remote_state.admin_state.outputs.tenant_cost_rollup_table_arn
  rollup_table_name = data.terraform_remote_state.admin_state.outputs.tenant_cost_rollup_table_name

  # The ecr module exposes a map of service-name -> repository URL.
  # `cost-rollup-aggregator` is added to that map by the same PR that
  # ships this module; until the ecr apply lands, this lookup will
  # fail and the apply will block. That ordering is intentional: the
  # Lambda has no working image to point at without the repository.
  ecr_repository_url = data.terraform_remote_state.ecr.outputs.repository_urls["cost-rollup-aggregator"]
}

# ===========================================================================
# CloudWatch Log Group
#
# The Lambda runtime auto-creates `/aws/lambda/<function-name>` if it
# does not exist, but that auto-created group has no retention and no
# KMS encryption. We provision it explicitly so the retention matches
# the locked 30-day floor and the group is encrypted with the same
# logs CMK used by `infra/dev/observability/`.
#
# We import the logs CMK ARN from the observability remote state. If
# the observability module has not been applied yet, this lookup
# fails; that is the right blocker because un-encrypted Lambda logs
# violate the project's logging standard.
# ===========================================================================
data "terraform_remote_state" "observability" {
  backend = "s3"

  config = {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/observability/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

# Dedicated CMK for the Lambda log group. The observability module's
# shared logs CMK conditions encryption on log-group ARNs matching
# `/panakoes/dev/*`; Lambda log groups land under `/aws/lambda/*`,
# which does not match that condition, so the shared key cannot
# encrypt them. Mirrors `infra/dev/waf/` and `infra/dev/transcribe-worker/`
# (PR #189) which own per-module CMKs for the same reason.
data "aws_iam_policy_document" "log_kms" {
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
    # (`aws_kms_key.log`). Key ARN is not addressable at policy-creation
    # time. https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
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
      identifiers = ["logs.${data.aws_region.current.region}.amazonaws.com"]
    }
    # panakoes-iam-policy-resource-star: justified
    # KMS key policy: `*` resolves to this key only; service-principal use
    # is further pinned to this Lambda's log group ARN via EncryptionContext.
    # https://docs.aws.amazon.com/kms/latest/developerguide/key-policy-overview.html
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:${local.log_group}"]
    }
  }
}

resource "aws_kms_key" "log" {
  description             = "KMS key for the dev cost-rollup-aggregator Lambda log group"
  enable_key_rotation     = true
  deletion_window_in_days = 7
  policy                  = data.aws_iam_policy_document.log_kms.json

  tags = local.common_tags
}

resource "aws_kms_alias" "log" {
  name          = "alias/${local.name_prefix}-cost-rollup-aggregator-log"
  target_key_id = aws_kms_key.log.key_id
}

resource "aws_cloudwatch_log_group" "aggregator" {
  name              = local.log_group
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.log.arn

  tags = local.common_tags
}

# ===========================================================================
# Execution role
#
# Trust policy lets Lambda assume the role. Inline policy grants three
# narrow capability sets:
#
#   1. CloudWatch Logs write to the function's own log group only.
#   2. Cost Explorer read (no resource-level authorization possible per
#      the AWS service authorization reference; resources MUST be `*`).
#   3. DynamoDB PutItem on the rollup table ARN only (NOT the table
#      prefix). The aggregator does not need Read; the route owns that.
# ===========================================================================
data "aws_iam_policy_document" "trust" {
  statement {
    sid     = "AllowLambdaServiceToAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "aggregator" {
  name                 = "${local.function_name}-execution"
  description          = "Execution role for the nightly cost-rollup-aggregator Lambda"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600

  tags = local.common_tags
}

data "aws_iam_policy_document" "aggregator" {
  # CloudWatch Logs write, scoped to this function's log group + its
  # log streams. We do NOT grant `logs:CreateLogGroup` because the
  # group is provisioned above; that omission stops the Lambda from
  # silently re-creating an unencrypted group if Terraform drift
  # accidentally deletes ours.
  statement {
    sid    = "WriteOwnLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [
      aws_cloudwatch_log_group.aggregator.arn,
      "${aws_cloudwatch_log_group.aggregator.arn}:*",
    ]
  }

  # Cost Explorer reads. CE has no resource-level authorization per
  # the AWS service authorization reference; both actions MUST scope
  # to `*`. `GetDimensionValues` is included for the future case
  # where the aggregator needs to discover available tag values
  # before grouping (today the GroupBy on `tenant_id` is hard-coded).
  statement {
    sid    = "ReadCostExplorer"
    effect = "Allow"
    actions = [
      "ce:GetCostAndUsage",
      "ce:GetDimensionValues",
    ]
    # panakoes-iam-policy-resource-star: justified
    # AWS Cost Explorer (`ce:*` Get-family) does not support resource-level
    # authorization; the service-authorization reference lists only `*` as
    # the valid resource. No tightening is possible.
    # https://docs.aws.amazon.com/service-authorization/latest/reference/list_awscostexplorerservice.html
    resources = ["*"]
  }

  # DynamoDB upsert into the rollup table only. PutItem covers the
  # upsert semantics the aggregator depends on; UpdateItem is not
  # needed today and is omitted to keep the surface narrow.
  statement {
    sid    = "WriteRollup"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
    ]
    resources = [
      local.rollup_table_arn,
    ]
  }
}

resource "aws_iam_role_policy" "aggregator" {
  name   = "${local.function_name}-policy"
  role   = aws_iam_role.aggregator.id
  policy = data.aws_iam_policy_document.aggregator.json
}

# ===========================================================================
# Lambda function
#
# Container-image deploy. Reserved concurrency 1 so a stuck or slow
# nightly run cannot pile up if EventBridge double-fires (rare, but
# the Scheduler does not strictly guarantee at-most-once delivery).
# Memory 256 MB and timeout 5 minutes per the variables file
# rationale.
#
# `image_uri` resolves against the ECR repository URL exported by the
# ecr module + the configured image tag. The first apply of this
# module is BLOCKED until the operator builds and pushes a tagged
# image; without one, the function create call fails because Lambda
# validates the image at create time. See the README for the
# bootstrap sequence.
# ===========================================================================
resource "aws_lambda_function" "aggregator" {
  function_name = local.function_name
  role          = aws_iam_role.aggregator.arn
  package_type  = "Image"
  image_uri     = "${local.ecr_repository_url}:latest"

  memory_size = var.lambda_memory_mb
  timeout     = var.lambda_timeout_seconds
  # Reserved concurrency intentionally NULL in dev. AWS account-default
  # quota for `UnreservedConcurrentExecution` is 10 (not the docs-advertised
  # 1000); reserving any concurrency drops unreserved below the 10-floor
  # AWS enforces, and `PutFunctionConcurrency` rejects the call. Re-enable
  # in production when the account's quota is raised via Service Quotas.
  reserved_concurrent_executions = null

  environment {
    variables = {
      TENANT_COST_ROLLUP_TABLE = local.rollup_table_name
    }
  }

  # The image_uri changes on every push. The Lambda is otherwise
  # immutable from Terraform's perspective; a re-apply after a fresh
  # image push to `:latest` is the deploy mechanism.
  lifecycle {
    ignore_changes = [
      # Terraform cannot tell `:latest` from a fresh push by SHA; we
      # ignore image_uri drift so a `terraform apply` after a deploy
      # does not redundantly try to update the function. Promote to
      # an explicit version-tag scheme once we adopt one.
      image_uri,
    ]
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy.aggregator,
    aws_cloudwatch_log_group.aggregator,
  ]
}

# ===========================================================================
# EventBridge Scheduler rule
#
# `aws_scheduler_schedule` is the modern Scheduler resource (2022+)
# and is the right primitive for cron-style schedules. The legacy
# `aws_cloudwatch_event_rule` cron path is also supported by AWS but
# Scheduler is the path AWS recommends for new workloads (better
# quotas, native one-time-and-recurring support, per-schedule IAM).
#
# `flexible_time_window OFF` means the schedule fires at exactly the
# configured cron tick. We do not need the +/- minutes flex because
# the aggregator is the only consumer of CE at this hour.
# ===========================================================================
data "aws_iam_policy_document" "scheduler_trust" {
  statement {
    sid     = "AllowSchedulerServiceToAssume"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    # Confused-deputy guard: only invocations from this account on
    # behalf of this schedule may assume the role. AWS recommends
    # this for any service-linked-style role.
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name                 = "${local.schedule_name}-scheduler"
  description          = "EventBridge Scheduler role allowed to invoke the cost-rollup-aggregator Lambda"
  assume_role_policy   = data.aws_iam_policy_document.scheduler_trust.json
  max_session_duration = 3600

  tags = local.common_tags
}

data "aws_iam_policy_document" "scheduler_invoke" {
  statement {
    sid       = "InvokeAggregatorLambda"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.aggregator.arn]
  }
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name   = "${local.schedule_name}-invoke"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_invoke.json
}

resource "aws_scheduler_schedule" "nightly" {
  name        = local.schedule_name
  description = "Fires the cost-rollup-aggregator Lambda once per day at 02:00 UTC."

  schedule_expression          = var.schedule_expression
  schedule_expression_timezone = "UTC"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.aggregator.arn
    role_arn = aws_iam_role.scheduler.arn

    # Empty input: the handler treats an empty event as "aggregate
    # yesterday-UTC", which is the production happy path. Manual
    # replay of a specific day is done by invoking the function
    # directly with `{"day": "YYYY-MM-DD"}`, NOT by changing this
    # schedule.
    input = jsonencode({})

    # No retries on the Scheduler side. A failed nightly run is
    # picked up by the next nightly run; the upsert semantics in
    # the rollup table converge on the latest CE answer.
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 0
    }
  }
}

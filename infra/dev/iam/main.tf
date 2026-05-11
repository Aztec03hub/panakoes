locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "iam"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # Services that run on ECS Fargate get both a task role (runtime
  # identity for application code) and an execution role (identity
  # the ECS agent uses to pull images, fetch secrets at startup, and
  # ship logs to CloudWatch Logs). Task and execution roles are
  # distinct so a compromise of the application code does not give
  # the attacker the agent's pull/log permissions.
  ecs_services = [
    "ingestion-api",
    "summarization",
    "notification",
    "query-api",
    "auth",
    "session-manager",
    "billing",
    "cost-api",
    "admin-api",
    "gpu-spawner",
    "health-aggregator",
  ]

  # Services that run as Lambda or as plain EC2 (no ECS agent) only
  # need a task-style role. Lambda's "execution role" is the runtime
  # identity, so we model it as the task role here and there is no
  # second execution role to provision.
  non_ecs_services = [
    "transcriber-batch",
    "transcriber-stream",
    "event-router",
    "transcribe-worker",
  ]

  # Pulled-from-storage outputs. Captured into locals so each policy
  # below reads a short name instead of a deep traversal.
  audio_uploads_bucket_arn  = data.terraform_remote_state.storage.outputs.audio_uploads_bucket_arn
  audio_uploads_kms_key_arn = data.terraform_remote_state.storage.outputs.audio_uploads_kms_key_arn
  transcripts_bucket_arn    = data.terraform_remote_state.storage.outputs.transcripts_bucket_arn
  transcripts_kms_key_arn   = data.terraform_remote_state.storage.outputs.transcripts_kms_key_arn

  # Pulled-from-data outputs.
  ingestion_table_arn          = data.terraform_remote_state.data.outputs.ingestion_table_arn
  audit_log_table_arn          = data.terraform_remote_state.data.outputs.audit_log_table_arn
  streaming_sessions_table_arn = data.terraform_remote_state.data.outputs.streaming_sessions_table_arn
  tenants_table_arn            = data.terraform_remote_state.data.outputs.tenants_table_arn
  api_keys_table_arn           = data.terraform_remote_state.data.outputs.api_keys_table_arn

  # AWS Batch job ARNs are dynamic (one per submitted job, generated at
  # submit time). admin-api's terminate-job lifecycle op needs to be
  # able to terminate any running Batch job in this account/region;
  # there is no per-job tagging convention to scope by today.
  batch_job_arn_pattern = "arn:aws:batch:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:job/*"

  # GSI ARNs are derived from the table ARN; per the data module
  # README, downstream policies use `${arn}/index/*` rather than
  # treating GSIs as separate resources.
  ingestion_table_indexes_arn          = "${local.ingestion_table_arn}/index/*"
  streaming_sessions_table_indexes_arn = "${local.streaming_sessions_table_arn}/index/*"

  # admin-state module outputs (cost-api / admin-api backing tables).
  cost_cache_table_arn         = data.terraform_remote_state.admin_state.outputs.cost_cache_table_arn
  tenant_cost_rollup_table_arn = data.terraform_remote_state.admin_state.outputs.tenant_cost_rollup_table_arn
  alert_state_table_arn        = data.terraform_remote_state.admin_state.outputs.alert_state_table_arn
  lifecycle_state_table_arn    = data.terraform_remote_state.admin_state.outputs.lifecycle_state_table_arn

  # Forward references for tables that don't exist yet. summaries and
  # sessions tables for query-api and summarization are tracked as
  # separate backlog items; we construct the ARNs explicitly so the
  # policies stay tight when the tables ship.
  summaries_table_arn         = "arn:aws:dynamodb:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${local.name_prefix}-summaries"
  notification_table_arn      = "arn:aws:dynamodb:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${local.name_prefix}-notifications"
  summaries_table_indexes_arn = "${local.summaries_table_arn}/index/*"
}

# ===========================================================================
# Assume-role trust policies
#
# Two trust policies cover everything in this module. ECS services
# trust ecs-tasks.amazonaws.com; Lambda services trust
# lambda.amazonaws.com; the GPU EC2 instance profile trusts
# ec2.amazonaws.com. We define each as a data source so the JSON
# stays explicit and reviewable in plan output.
# ===========================================================================

data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "ec2_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# ===========================================================================
# ECS task execution roles (one per ECS service)
#
# The execution role is what the ECS agent itself uses, not the
# application. Common scope: pull container images from ECR, ship
# stdout/stderr to CloudWatch Logs, and (optionally) read Secrets
# Manager values into environment variables at task startup.
#
# We attach the AWS-managed AmazonECSTaskExecutionRolePolicy which
# already covers ECR + CloudWatch Logs at the AWS-recommended scope,
# then layer an inline policy that scopes Secrets Manager
# GetSecretValue to only the secrets each task references.
# ===========================================================================

resource "aws_iam_role" "execution" {
  for_each = toset(local.ecs_services)

  name                 = "${local.name_prefix}-${each.value}-execution"
  description          = "ECS task execution role for the ${each.value} service in the dev environment"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = each.value
    Role    = "execution"
  })
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  for_each = aws_iam_role.execution

  role       = each.value.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Per-service Secrets Manager access for the execution role. The set
# is the union of secrets each service needs at startup. ECS injects
# these into the task's environment variables before the application
# starts.
locals {
  execution_secret_arns = {
    ingestion-api     = [local.secret_arns.jwt_signing, local.secret_arns.database_url]
    summarization     = [local.secret_arns.jwt_signing, local.secret_arns.anthropic_api_key]
    notification      = [local.secret_arns.jwt_signing, local.secret_arns.ses_smtp]
    query-api         = [local.secret_arns.jwt_signing]
    auth              = [local.secret_arns.jwt_signing, local.secret_arns.database_url]
    session-manager   = [local.secret_arns.jwt_signing]
    billing           = [local.secret_arns.jwt_signing, local.secret_arns.stripe_test_key, local.secret_arns.stripe_webhook_signing]
    cost-api          = [local.secret_arns.jwt_signing]
    admin-api         = [local.secret_arns.jwt_signing]
    gpu-spawner       = [local.secret_arns.jwt_signing]
    health-aggregator = [local.secret_arns.jwt_signing]
  }
}

data "aws_iam_policy_document" "execution_secrets" {
  for_each = local.execution_secret_arns

  statement {
    sid       = "ReadStartupSecrets"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = each.value
  }

  # ECS pulls secret values via the execution role at task start.
  # Secrets in `panakoes-dev/*` are encrypted with the secrets-module
  # CMK; without `kms:Decrypt` on that key, `GetSecretValue` returns
  # `AccessDeniedException: Access to KMS is not allowed.` Scope the
  # decrypt strictly to the secrets CMK plus the secretsmanager
  # service via condition so this role cannot decrypt unrelated keys.
  statement {
    sid       = "DecryptStartupSecretsKMS"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [data.terraform_remote_state.secrets.outputs.kms_key_arn]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${data.aws_region.current.region}.amazonaws.com"]
    }
  }
}

resource "aws_iam_policy" "execution_secrets" {
  for_each = data.aws_iam_policy_document.execution_secrets

  name        = "${local.name_prefix}-${each.key}-execution-secrets"
  description = "Secrets Manager GetSecretValue scoped to the secrets the ${each.key} ECS task references at startup."
  policy      = each.value.json

  tags = merge(local.common_tags, {
    Service = each.key
    Role    = "execution"
  })
}

resource "aws_iam_role_policy_attachment" "execution_secrets" {
  for_each = aws_iam_policy.execution_secrets

  role       = aws_iam_role.execution[each.key].name
  policy_arn = each.value.arn
}

# ===========================================================================
# Task roles (one per service, ECS or otherwise)
#
# These are the runtime identities the application code assumes. The
# permission set per service is intentionally minimal; resources are
# explicit ARNs and conditions tighten further wherever AWS supports
# it.
# ===========================================================================

# ---------------------------------------------------------------------------
# ingestion-api
# ---------------------------------------------------------------------------

resource "aws_iam_role" "ingestion_api" {
  name                 = "${local.name_prefix}-ingestion-api-task"
  description          = "Runtime IAM role for the ingestion-api ECS task in dev"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "ingestion-api"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "ingestion_api" {
  statement {
    sid       = "PutAudioObjects"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${local.audio_uploads_bucket_arn}/audio/*"]
  }

  statement {
    sid     = "WriteIngestionRecord"
    effect  = "Allow"
    actions = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query"]
    resources = [
      local.ingestion_table_arn,
      local.ingestion_table_indexes_arn,
    ]
  }

  statement {
    sid       = "EncryptDecryptAudioBucket"
    effect    = "Allow"
    actions   = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [local.audio_uploads_kms_key_arn]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "ingestion_api" {
  name   = "${local.name_prefix}-ingestion-api-policy"
  role   = aws_iam_role.ingestion_api.id
  policy = data.aws_iam_policy_document.ingestion_api.json
}

# ---------------------------------------------------------------------------
# summarization
# ---------------------------------------------------------------------------

resource "aws_iam_role" "summarization" {
  name                 = "${local.name_prefix}-summarization-task"
  description          = "Runtime IAM role for the summarization ECS task in dev"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "summarization"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "summarization" {
  statement {
    sid       = "ReadTranscripts"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${local.transcripts_bucket_arn}/*"]
  }

  statement {
    sid       = "WriteSummariesToTranscriptsBucket"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${local.transcripts_bucket_arn}/summaries/*"]
  }

  statement {
    sid     = "WriteSummariesTable"
    effect  = "Allow"
    actions = ["dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [
      local.summaries_table_arn,
      local.summaries_table_indexes_arn,
    ]
  }

  statement {
    sid       = "DecryptTranscriptsBucket"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [local.transcripts_kms_key_arn]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "summarization" {
  name   = "${local.name_prefix}-summarization-policy"
  role   = aws_iam_role.summarization.id
  policy = data.aws_iam_policy_document.summarization.json
}

# ---------------------------------------------------------------------------
# notification
# ---------------------------------------------------------------------------

resource "aws_iam_role" "notification" {
  name                 = "${local.name_prefix}-notification-task"
  description          = "Runtime IAM role for the notification ECS task in dev"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "notification"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "notification" {
  # SES SendEmail requires Resource = "*" because the AWS API does
  # not support per-identity ARN authorization on SendEmail/SendRawEmail
  # without using ses:FromAddress as a condition. We pin the From
  # address to the verified domain via that condition, which is the
  # AWS-documented way to scope SES sends to one domain.
  statement {
    sid       = "SendEmailFromVerifiedDomain"
    effect    = "Allow"
    actions   = ["ses:SendEmail", "ses:SendRawEmail"]
    resources = ["*"]

    condition {
      test     = "StringLike"
      variable = "ses:FromAddress"
      values   = ["*@${var.ses_sending_domain}"]
    }
  }

  statement {
    sid     = "WriteNotificationRecord"
    effect  = "Allow"
    actions = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [
      local.notification_table_arn,
      "${local.notification_table_arn}/index/*",
    ]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "notification" {
  name   = "${local.name_prefix}-notification-policy"
  role   = aws_iam_role.notification.id
  policy = data.aws_iam_policy_document.notification.json
}

# ---------------------------------------------------------------------------
# query-api  (READ-ONLY across ingestion, summaries, sessions)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "query_api" {
  name                 = "${local.name_prefix}-query-api-task"
  description          = "Runtime IAM role for the query-api ECS task in dev. Read-only access to ingestion, summaries, and streaming-sessions tables."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "query-api"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "query_api" {
  # Explicitly only Query and GetItem; no Put/Update/Delete. The
  # query-api is read-only by design.
  statement {
    sid     = "ReadIngestion"
    effect  = "Allow"
    actions = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:BatchGetItem"]
    resources = [
      local.ingestion_table_arn,
      local.ingestion_table_indexes_arn,
    ]
  }

  statement {
    sid     = "ReadSummaries"
    effect  = "Allow"
    actions = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:BatchGetItem"]
    resources = [
      local.summaries_table_arn,
      local.summaries_table_indexes_arn,
    ]
  }

  statement {
    sid     = "ReadStreamingSessions"
    effect  = "Allow"
    actions = ["dynamodb:Query", "dynamodb:GetItem", "dynamodb:BatchGetItem"]
    resources = [
      local.streaming_sessions_table_arn,
      local.streaming_sessions_table_indexes_arn,
    ]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "query_api" {
  name   = "${local.name_prefix}-query-api-policy"
  role   = aws_iam_role.query_api.id
  policy = data.aws_iam_policy_document.query_api.json
}

# ---------------------------------------------------------------------------
# auth
#
# The auth service talks to Postgres directly through the VPC, not
# via the RDS Data API, so its IAM grants are limited to Secrets
# Manager (database URL + JWT signing key) and the audit log. The
# database authentication itself is enforced by Postgres credentials
# fetched from Secrets Manager and by VPC security group rules, both
# of which live outside this IAM module.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "auth" {
  name                 = "${local.name_prefix}-auth-task"
  description          = "Runtime IAM role for the auth ECS task in dev. Postgres access is via VPC + security groups, not IAM."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "auth"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "auth" {
  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "auth" {
  name   = "${local.name_prefix}-auth-policy"
  role   = aws_iam_role.auth.id
  policy = data.aws_iam_policy_document.auth.json
}

# ---------------------------------------------------------------------------
# session-manager
# ---------------------------------------------------------------------------

resource "aws_iam_role" "session_manager" {
  name                 = "${local.name_prefix}-session-manager-task"
  description          = "Runtime IAM role for the session-manager ECS task in dev. Full CRUD on the streaming-sessions table."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "session-manager"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "session_manager" {
  statement {
    sid    = "FullCrudOnStreamingSessions"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
    ]
    resources = [
      local.streaming_sessions_table_arn,
      local.streaming_sessions_table_indexes_arn,
    ]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "session_manager" {
  name   = "${local.name_prefix}-session-manager-policy"
  role   = aws_iam_role.session_manager.id
  policy = data.aws_iam_policy_document.session_manager.json
}

# ---------------------------------------------------------------------------
# gpu-spawner (ECS Fargate task role)
#
# Runs on ECS Fargate (pivoted from the original Lambda plan; the
# RunInstances workload is request/response, but a long-running
# Fargate task gives us the same NLB + JWT-validation shape as the
# other tier-2/3 services without per-cold-start latency on each
# session-manager call). Launches and terminates EC2 GPU instances
# that host the streaming faster-whisper transcriber.
# ec2:RunInstances is constrained by instance type and a tag-on-create
# requirement that every launched instance carries `Project=panakoes`
# and `Spawner=panakoes-dev-gpu-spawner`. ec2:TerminateInstances is
# restricted to instances that already carry those tags.
#
# The ECS task execution role (separate identity, provisioned by the
# `aws_iam_role.execution` for_each loop because `gpu-spawner` is now
# in `local.ecs_services`) handles image pull + log shipping + Secrets
# Manager fetch at task start.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "gpu_spawner" {
  name                 = "${local.name_prefix}-gpu-spawner-task"
  description          = "Runtime IAM role for the gpu-spawner ECS task. Launches/terminates tagged g4dn.xlarge instances for streaming transcription."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "gpu-spawner"
    Role    = "task"
  })
}

# Instance profile + role the gpu-spawner passes to the EC2
# instances it launches. Defining the role here lets us iam:PassRole
# on a tightly-scoped ARN instead of "*". The role itself starts
# empty; the streaming AMI bring-up scripts attach whatever the
# transcriber actually needs (S3 PutObject on transcripts, SSM
# session manager for ops access). Those grants land in a follow-up.
resource "aws_iam_role" "gpu_instance" {
  name                 = "${local.name_prefix}-gpu-instance"
  description          = "Instance-profile role attached to GPU EC2 instances launched by gpu-spawner. Permissions added in a follow-up; placeholder for least-privilege scoping."
  assume_role_policy   = data.aws_iam_policy_document.ec2_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "gpu-instance"
    Role    = "instance"
  })
}

resource "aws_iam_instance_profile" "gpu_instance" {
  name = "${local.name_prefix}-gpu-instance"
  role = aws_iam_role.gpu_instance.name

  tags = local.common_tags
}

data "aws_iam_policy_document" "gpu_spawner" {
  # RunInstances requires the caller to carry permissions on every
  # resource type the API touches. We constrain instance type and
  # require the launched instance to be tagged on creation.
  statement {
    sid     = "LaunchGpuInstance"
    effect  = "Allow"
    actions = ["ec2:RunInstances"]
    resources = [
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:instance/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "ec2:InstanceType"
      values   = var.gpu_instance_types
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = [var.project_name]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Spawner"
      values   = ["${local.name_prefix}-gpu-spawner"]
    }

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["Project", "Spawner", "SessionId", "Environment"]
    }
  }

  # The other resource types RunInstances touches do not need tag
  # gating but still must be listed; we scope to caller's account.
  # Volumes and network interfaces inherit the caller's account.
  statement {
    sid     = "RunInstancesSupportingResources"
    effect  = "Allow"
    actions = ["ec2:RunInstances"]
    resources = [
      "arn:aws:ec2:${data.aws_region.current.region}::image/*",
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:volume/*",
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:network-interface/*",
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:subnet/*",
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:security-group/*",
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:key-pair/*",
    ]
  }

  # Tagging the new instance happens as a sub-call of RunInstances;
  # without ec2:CreateTags scoped to RunInstances the launch fails.
  statement {
    sid     = "TagOnRunInstances"
    effect  = "Allow"
    actions = ["ec2:CreateTags"]
    resources = [
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:instance/*",
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:volume/*",
      "arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:network-interface/*",
    ]

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["RunInstances"]
    }
  }

  # Termination is gated to instances we ourselves launched. The two
  # tags we required at launch are the only valid match here.
  statement {
    sid       = "TerminateOwnInstances"
    effect    = "Allow"
    actions   = ["ec2:TerminateInstances"]
    resources = ["arn:aws:ec2:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Project"
      values   = [var.project_name]
    }

    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Spawner"
      values   = ["${local.name_prefix}-gpu-spawner"]
    }
  }

  # DescribeInstances has no resource-level authorization in the AWS
  # API; this is the documented exception requiring "*". We scope to
  # the read-only Describe verbs only.
  statement {
    sid       = "DescribeForLifecycleManagement"
    effect    = "Allow"
    actions   = ["ec2:DescribeInstances", "ec2:DescribeInstanceStatus", "ec2:DescribeImages"]
    resources = ["*"]
  }

  # PassRole only on the instance-profile role we created above. This
  # is the load-bearing privilege escalation guard: without this
  # constraint, RunInstances + iam:PassRole on "*" would let the
  # spawner attach an arbitrary IAM role to the EC2 it launched.
  statement {
    sid       = "PassGpuInstanceRoleOnly"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.gpu_instance.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }

  statement {
    sid       = "ReadJwtSigningSecret"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [local.secret_arns.jwt_signing]
  }
}

resource "aws_iam_role_policy" "gpu_spawner" {
  name   = "${local.name_prefix}-gpu-spawner-policy"
  role   = aws_iam_role.gpu_spawner.id
  policy = data.aws_iam_policy_document.gpu_spawner.json
}

# ---------------------------------------------------------------------------
# transcriber-batch (Lambda or Batch task)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "transcriber_batch" {
  name                 = "${local.name_prefix}-transcriber-batch-task"
  description          = "Runtime IAM role for the async batch transcription worker. Reads audio uploads, writes transcripts, updates the ingestion record status."
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "transcriber-batch"
    Role    = "task"
  })
}

resource "aws_iam_role_policy_attachment" "transcriber_batch_logs" {
  role       = aws_iam_role.transcriber_batch.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "transcriber_batch" {
  statement {
    sid       = "ReadAudioObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${local.audio_uploads_bucket_arn}/audio/*"]
  }

  statement {
    sid       = "WriteTranscripts"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${local.transcripts_bucket_arn}/*"]
  }

  statement {
    sid       = "UpdateIngestionStatus"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [local.ingestion_table_arn]
  }

  statement {
    sid       = "DecryptAudioBucket"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [local.audio_uploads_kms_key_arn]
  }

  statement {
    sid       = "EncryptTranscriptsBucket"
    effect    = "Allow"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [local.transcripts_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "transcriber_batch" {
  name   = "${local.name_prefix}-transcriber-batch-policy"
  role   = aws_iam_role.transcriber_batch.id
  policy = data.aws_iam_policy_document.transcriber_batch.json
}

# ---------------------------------------------------------------------------
# transcriber-stream (runs on the GPU EC2 instance)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "transcriber_stream" {
  name                 = "${local.name_prefix}-transcriber-stream-task"
  description          = "Runtime IAM role for the streaming transcription worker. Writes transcripts, updates streaming-sessions, emits custom CloudWatch metrics."
  assume_role_policy   = data.aws_iam_policy_document.ec2_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "transcriber-stream"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "transcriber_stream" {
  statement {
    sid       = "WriteTranscripts"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${local.transcripts_bucket_arn}/*"]
  }

  statement {
    sid       = "UpdateStreamingSession"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [local.streaming_sessions_table_arn]
  }

  # PutMetricData has no resource-level authorization in the AWS API;
  # the documented exception requires Resource = "*". We scope it
  # tightly with the cloudwatch:namespace condition key so this role
  # can only publish to the panakoes/transcribe namespace.
  statement {
    sid       = "PutCustomMetrics"
    effect    = "Allow"
    actions   = ["cloudwatch:PutMetricData"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["panakoes/transcribe"]
    }
  }

  statement {
    sid       = "EncryptTranscriptsBucket"
    effect    = "Allow"
    actions   = ["kms:Encrypt", "kms:GenerateDataKey", "kms:DescribeKey"]
    resources = [local.transcripts_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "transcriber_stream" {
  name   = "${local.name_prefix}-transcriber-stream-policy"
  role   = aws_iam_role.transcriber_stream.id
  policy = data.aws_iam_policy_document.transcriber_stream.json
}

# ---------------------------------------------------------------------------
# event-router (Lambda)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "event_router" {
  name                 = "${local.name_prefix}-event-router-task"
  description          = "Runtime IAM role for the event-router Lambda. Drives the transcription pipeline from S3 events into downstream services."
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "event-router"
    Role    = "task"
  })
}

resource "aws_iam_role_policy_attachment" "event_router_logs" {
  role       = aws_iam_role.event_router.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "event_router" {
  statement {
    sid       = "ReadAudioObjects"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${local.audio_uploads_bucket_arn}/audio/*"]
  }

  statement {
    sid       = "UpdateIngestionRecord"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [local.ingestion_table_arn]
  }

  statement {
    sid       = "PutEventsOnProjectBus"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = [local.eventbridge_bus_arn]
  }

  statement {
    sid       = "InvokePipelineLambdas"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = local.router_target_lambda_arns
  }

  statement {
    sid       = "DecryptAudioBucket"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:DescribeKey"]
    resources = [local.audio_uploads_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "event_router" {
  name   = "${local.name_prefix}-event-router-policy"
  role   = aws_iam_role.event_router.id
  policy = data.aws_iam_policy_document.event_router.json
}

# ---------------------------------------------------------------------------
# transcribe-worker (Lambda)
#
# Auto-trigger consumer for the SQS queue that EventBridge fans S3
# ObjectCreated events into. The role itself + the AWSLambdaBasicExecutionRole
# attachment live here (matching the event-router pattern); the
# resource-tight inline policy (SQS, S3, KMS, DynamoDB, Secrets Manager)
# lives in `infra/dev/transcribe-worker/main.tf` because it depends on
# resources that module owns (SQS queue ARN, queue CMK ARN). Splitting
# is intentional: this module owns the IDENTITY, the consumer module
# owns the resource-bound POLICIES. The brief asked the IAM role to
# land here, mirroring the per-service-task-role pattern.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "transcribe_worker" {
  name                 = "${local.name_prefix}-transcribe-worker-task"
  description          = "Runtime IAM role for the transcribe-worker Lambda. Consumes the auto-transcription SQS trigger queue and re-uses ingestion-api's transcribe_ingestion orchestration."
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "transcribe-worker"
    Role    = "task"
  })
}

resource "aws_iam_role_policy_attachment" "transcribe_worker_logs" {
  role       = aws_iam_role.transcribe_worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "transcribe_worker_xray" {
  role       = aws_iam_role.transcribe_worker.name
  policy_arn = "arn:aws:iam::aws:policy/AWSXRayDaemonWriteAccess"
}

# ---------------------------------------------------------------------------
# billing
#
# Stripe webhook receiver and subscription state machine. References a
# `panakoes-dev-billing-events` table that does not yet exist in
# `infra/dev/data/`. The table will land when the billing slice is
# implemented; the policy here is correct the moment the table
# exists, and prior to that no broader access is silently granted.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "billing" {
  name                 = "${local.name_prefix}-billing-task"
  description          = "Runtime IAM role for the billing ECS task in dev. Stripe webhook receiver; reads/writes the (forthcoming) billing-events table."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "billing"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "billing" {
  statement {
    sid    = "FullCrudOnBillingEvents"
    effect = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [
      local.billing_events_table_arn,
      "${local.billing_events_table_arn}/index/*",
    ]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "billing" {
  name   = "${local.name_prefix}-billing-policy"
  role   = aws_iam_role.billing.id
  policy = data.aws_iam_policy_document.billing.json
}

# ---------------------------------------------------------------------------
# cost-api  (Tier 2 admin dashboard backend)
#
# Reads AWS Cost Explorer, caches results in DynamoDB, and writes audit
# events on every admin call. Permissions are scoped to:
#
#   - ce:GetCostAndUsage / GetCostForecast / GetCostAndUsageWithResources:
#     Cost Explorer has no resource-level authorization; the API itself
#     is gated only by IAM action. Wildcard resource is required by the
#     AWS API surface.
#   - dynamodb on cost-cache: read AND write (cache fills on miss).
#   - dynamodb on tenant-cost-rollup: read-write so the Phase 2 nightly
#     aggregator job can populate it; the route layer only reads.
#   - dynamodb on alert-state: read-write so the anomaly detector can
#     dedup signatures.
#   - dynamodb:PutItem on audit-log: every admin call logs an event.
#
# Step-up MFA is enforced at the application layer (Better-Auth +
# admin role). IAM does not enforce it here because the JWT has
# already been validated by the time the boto3 call fires; the IAM
# trust boundary is "only the cost-api service can assume this role
# at all".
# ---------------------------------------------------------------------------

resource "aws_iam_role" "cost_api" {
  name                 = "${local.name_prefix}-cost-api-task"
  description          = "Runtime IAM role for the cost-api ECS task in dev"
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "cost-api"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "cost_api" {
  statement {
    sid    = "ReadCostExplorer"
    effect = "Allow"
    actions = [
      "ce:GetCostAndUsage",
      "ce:GetCostForecast",
      "ce:GetCostAndUsageWithResources",
      "ce:GetDimensionValues",
    ]
    # Cost Explorer has no resource-level authorization. Per AWS docs:
    # https://docs.aws.amazon.com/service-authorization/latest/reference/list_awsbillingandcostmanagement.html
    resources = ["*"]
  }

  statement {
    sid       = "ReadWriteCostCache"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem"]
    resources = [local.cost_cache_table_arn]
  }

  statement {
    sid       = "ReadWriteTenantCostRollup"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [local.tenant_cost_rollup_table_arn]
  }

  statement {
    sid       = "ReadWriteAlertState"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
    resources = [local.alert_state_table_arn]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }
}

resource "aws_iam_role_policy" "cost_api" {
  name   = "${local.name_prefix}-cost-api-policy"
  role   = aws_iam_role.cost_api.id
  policy = data.aws_iam_policy_document.cost_api.json
}

# ---------------------------------------------------------------------------
# admin-api  (Tier 3 admin dashboard backend, lifecycle operations)
#
# admin-api is the most dangerous code path in the system. The IAM
# trust boundary is "only the admin-api ECS service can assume this
# role." Application-layer gates (typed confirmation, idempotency-by-key,
# step-up MFA, audit-before-AND-after) are documented in ADR-032.
#
# Permissions are scoped per-table:
#
#   - lifecycle-state: full CRUD. The orchestrator writes the pending
#     row, reads it for idempotency replay, and updates it to the
#     terminal status.
#   - audit-log: PutItem only. Every operation writes the intent +
#     outcome rows; no admin-api code path reads from this table
#     directly (the Tier 3.3 audit-log read view runs through the
#     Tier3ActionIndex GSI; same PutItem-only access is sufficient
#     for the writer).
#   - streaming-sessions: UpdateItem + GetItem + Query so the
#     terminate-session operation can mutate session rows. The
#     ConditionExpression on attribute_exists(session_id) keeps the
#     blast radius scoped to existing rows; admin-api cannot create
#     phantom session rows.
#   - ingestion: UpdateItem + GetItem + Query for the Phase 3.2-extended
#     force-fail-ingestion / purge-tenant-data operations. The role
#     gets the permission now so adding those ops in a follow-up PR
#     does not require a fresh IAM apply that could surprise an
#     operator running terraform plan.
#
# admin-api intentionally does NOT have:
#   - Cost Explorer access (Tier 3 reads no cost data; that's cost-api)
#   - Secrets Manager access beyond the jwt-signing-secret read at
#     task-execution time
#   - IAM mutation (rotating IAM keys is a separate, higher-trust
#     operation that does not run through admin-api)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "admin_api" {
  name                 = "${local.name_prefix}-admin-api-task"
  description          = "Runtime IAM role for the admin-api ECS task in dev. Tier 3 lifecycle operations."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "admin-api"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "admin_api" {
  statement {
    sid    = "ReadWriteLifecycleState"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
    ]
    resources = [local.lifecycle_state_table_arn]
  }

  statement {
    sid       = "WriteAuditLog"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [local.audit_log_table_arn]
  }

  statement {
    sid    = "MutateStreamingSessions"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [
      local.streaming_sessions_table_arn,
      local.streaming_sessions_table_indexes_arn,
    ]
  }

  statement {
    sid    = "MutateIngestion"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:UpdateItem",
    ]
    resources = [
      local.ingestion_table_arn,
      local.ingestion_table_indexes_arn,
    ]
  }

  # Tier 3 lifecycle ops on tenants + api-keys tables. Point-lookup
  # by id (GetItem) for replay/idempotency reads, UpdateItem for the
  # status mutation (suspend / reactivate / revoke). No PutItem (rows
  # are created by the auth/billing flow, not by admin-api) and no
  # DeleteItem (Tier 3 ops mark rows revoked/suspended; hard-delete is
  # a separate, higher-trust purge path that does not run here).
  statement {
    sid    = "MutateTenantsAndApiKeys"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:UpdateItem",
    ]
    resources = [
      local.tenants_table_arn,
      local.api_keys_table_arn,
    ]
  }

  # Tier 3 lifecycle ops emit domain events on the project EventBridge
  # bus (e.g. tenant.suspended, api_key.revoked) so downstream services
  # (notification, audit-aggregator, future webhook fanout) can react
  # without admin-api invoking each consumer directly. Scoped to the
  # project bus only.
  statement {
    sid       = "PutLifecycleEventsOnProjectBus"
    effect    = "Allow"
    actions   = ["events:PutEvents"]
    resources = [local.eventbridge_bus_arn]
  }

  # Tier 3 terminate-job lifecycle op cancels in-flight AWS Batch
  # transcription jobs. Job ARNs are generated at submit time, so the
  # resource is necessarily a per-job wildcard; the action verb is
  # narrow (TerminateJob only, no submit/describe).
  statement {
    sid       = "TerminateBatchJob"
    effect    = "Allow"
    actions   = ["batch:TerminateJob"]
    resources = [local.batch_job_arn_pattern]
  }
}

resource "aws_iam_role_policy" "admin_api" {
  name   = "${local.name_prefix}-admin-api-policy"
  role   = aws_iam_role.admin_api.id
  policy = data.aws_iam_policy_document.admin_api.json
}

# ---------------------------------------------------------------------------
# health-aggregator  (Tier 1 admin dashboard backend)
#
# Read-only across three AWS APIs the aggregator polls:
#
#   - ecs:DescribeServices on the panakoes-dev cluster (every monitored
#     service runs there). Scoped to the cluster ARN; DescribeServices
#     supports resource-level authorization on the cluster.
#   - elbv2:DescribeTargetHealth on the dev target groups. The AWS
#     DescribeTargetHealth action accepts the target group ARN as the
#     resource; we pattern-match all target groups in the account/region
#     because the aggregator polls the live set returned by
#     DescribeTargetGroups (also scoped here). Production tightening:
#     enumerate the explicit target group ARNs once the set is stable.
#   - logs:FilterLogEvents on each `/panakoes/dev/<service>` log group
#     for the heartbeat heuristic. Scoped to the prefix; the wildcard
#     stays inside the project namespace and does not reach
#     `/aws/lambda/*` or any other log group ARN.
#
# No write actions, no Cost Explorer, no Secrets Manager (beyond
# jwt-signing-secret read at startup via the execution role), no
# DynamoDB.
# ---------------------------------------------------------------------------

resource "aws_iam_role" "health_aggregator" {
  name                 = "${local.name_prefix}-health-aggregator-task"
  description          = "Runtime IAM role for the health-aggregator ECS task in dev. Read-only across ECS DescribeServices, ELBv2 DescribeTargetHealth, and CloudWatch Logs FilterLogEvents on per-service log groups."
  assume_role_policy   = data.aws_iam_policy_document.ecs_tasks_trust.json
  max_session_duration = 3600

  tags = merge(local.common_tags, {
    Service = "health-aggregator"
    Role    = "task"
  })
}

data "aws_iam_policy_document" "health_aggregator" {
  # ECS DescribeServices on the dev cluster. Resource is the cluster
  # ARN; DescribeServices supports cluster-scoped authorization. We do
  # not list service ARNs because the registry lives in service code
  # and the aggregator polls the full set the cluster reports.
  statement {
    sid       = "DescribeEcsServices"
    effect    = "Allow"
    actions   = ["ecs:DescribeServices", "ecs:ListServices"]
    resources = ["arn:aws:ecs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:cluster/${local.name_prefix}", "arn:aws:ecs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:service/${local.name_prefix}/*"]
  }

  # ELBv2 read-only on the dev account/region target groups + load
  # balancers. DescribeTargetHealth is the load-bearing call; the two
  # Describe* companions let the aggregator resolve target groups to
  # their associated load balancers for context. ELBv2 Describe*
  # actions do not support resource-level authorization; the AWS API
  # rejects ARN-scoped resources on these verbs and requires "*". We
  # constrain by action verb only.
  statement {
    sid       = "DescribeTargetHealth"
    effect    = "Allow"
    actions   = ["elasticloadbalancing:DescribeTargetHealth", "elasticloadbalancing:DescribeTargetGroups", "elasticloadbalancing:DescribeLoadBalancers"]
    resources = ["*"]
  }

  # CloudWatch Logs FilterLogEvents on each per-service log group.
  # Scoped to the `/panakoes/dev/*` prefix; `:log-stream:*` covers all
  # streams within each group (FilterLogEvents authorizes against the
  # log-group ARN with a trailing `:*` per AWS docs). DescribeLogGroups
  # is needed so the aggregator can confirm a log group exists before
  # filtering.
  statement {
    sid    = "FilterServiceLogs"
    effect = "Allow"
    actions = [
      "logs:FilterLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = [
      "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/${var.project_name}/${var.environment}/*",
      "arn:aws:logs:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:log-group:/${var.project_name}/${var.environment}/*:log-stream:*",
    ]
  }
}

resource "aws_iam_role_policy" "health_aggregator" {
  name   = "${local.name_prefix}-health-aggregator-policy"
  role   = aws_iam_role.health_aggregator.id
  policy = data.aws_iam_policy_document.health_aggregator.json
}

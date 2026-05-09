# Account and region data sources used to build explicit ARNs for
# resources that do not yet have a Terraform module of their own
# (Secrets Manager secrets, EventBridge bus, downstream Lambdas, the
# CloudWatch metric namespace). Building the ARN explicitly with the
# caller-identity account ID keeps these policies identical across
# environments without hardcoding the account number in source.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Storage module remote state: S3 buckets and their KMS CMKs.
# Provides ARNs for the audio-uploads and transcripts buckets, plus
# the per-bucket CMK ARNs we condition KMS Encrypt/Decrypt on.
data "terraform_remote_state" "storage" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/storage/terraform.tfstate"
    region = "us-east-1"
  }
}

# Data module remote state: DynamoDB tables (ingestion, audit log,
# streaming sessions). Tables get referenced by ARN; GSI ARNs are
# derived inline as `${table_arn}/index/*` per the data module README.
data "terraform_remote_state" "data" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/data/terraform.tfstate"
    region = "us-east-1"
  }
}

# admin-state module remote state: DynamoDB tables backing the admin
# dashboard's Tier 2 (cost-cache, tenant-cost-rollup, alert-state) and
# Tier 3 (lifecycle-state) features. cost-api and admin-api task roles
# defined in main.tf consume these ARNs to grant least-privilege access
# scoped per service.
data "terraform_remote_state" "admin_state" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/admin-state/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Secrets Manager ARN construction
#
# A dedicated `infra/dev/secrets/` Terraform module does not exist yet
# (tracked separately in the backlog). Until it does, we construct the
# ARNs of the secrets we know exist with the documented Secrets
# Manager pattern:
#
#   arn:aws:secretsmanager:<region>:<account-id>:secret:<name>-??????
#
# The trailing six question marks are AWS's wildcard for the
# auto-generated random suffix every secret receives. This is the
# AWS-recommended way to reference a logical secret name without
# pinning the random suffix in code (which would break the policy if
# the secret is ever recreated). When `infra/dev/secrets/` lands, we
# replace these locals with `terraform_remote_state` outputs and the
# policies stay identical.
# ---------------------------------------------------------------------------
locals {
  # Used to build per-secret ARNs.
  secrets_manager_arn_prefix = "arn:aws:secretsmanager:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:secret"

  # Secret names align with `infra/dev/secrets/` module: `panakoes-dev/<name>`
  # (hyphen separator, not slash; module uses `panakoes-dev/jwt-signing-secret`,
  # `panakoes-dev/ses-smtp-credentials`, etc).
  secret_arns = {
    jwt_signing            = "${local.secrets_manager_arn_prefix}:${var.project_name}-${var.environment}/jwt-signing-secret-??????"
    database_url           = "${local.secrets_manager_arn_prefix}:${var.project_name}-${var.environment}/database-url-??????"
    anthropic_api_key      = "${local.secrets_manager_arn_prefix}:${var.project_name}-${var.environment}/anthropic-api-key-??????"
    ses_smtp               = "${local.secrets_manager_arn_prefix}:${var.project_name}-${var.environment}/ses-smtp-credentials-??????"
    stripe_test_key        = "${local.secrets_manager_arn_prefix}:${var.project_name}-${var.environment}/stripe-test-key-??????"
    stripe_webhook_signing = "${local.secrets_manager_arn_prefix}:${var.project_name}-${var.environment}/stripe-webhook-signing-secret-??????"
  }

  # ECS task execution role needs Secrets Manager read on the union of
  # every secret any service references at task startup.
  all_secret_arns = values(local.secret_arns)
}

# ---------------------------------------------------------------------------
# Billing-events table ARN (forward reference)
#
# The billing service writes to a `panakoes-dev-billing-events`
# DynamoDB table that does not yet exist in `infra/dev/data/`. The
# table will land when the billing slice is implemented. We build the
# ARN explicitly here so the policy stays correct the moment the
# table is created. Before that point, the billing role exists but
# its DynamoDB calls return ResourceNotFoundException; nothing
# silently grants broader access.
# ---------------------------------------------------------------------------
locals {
  billing_events_table_arn = "arn:aws:dynamodb:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:table/${var.project_name}-${var.environment}-billing-events"
}

# ---------------------------------------------------------------------------
# Other forward-referenced ARNs
#
# event-router needs to PutEvents on a project EventBridge bus and
# Invoke specific downstream Lambdas. Neither exists yet; we
# construct the ARNs here so the policy is ready to apply the moment
# those resources are created. Wildcard-by-name is preferred over
# Resource = "*" so the blast radius stays tight.
# ---------------------------------------------------------------------------
locals {
  eventbridge_bus_arn = "arn:aws:events:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:event-bus/${var.project_name}-${var.environment}"

  # event-router fans out to summarization, notification, and
  # transcriber-batch-trigger Lambdas. We list those Lambda ARNs by
  # name pattern; matching every project Lambda is wider than ideal,
  # but tightening to specific function names requires those Lambdas
  # to exist first. Tracked as a follow-up.
  router_target_lambda_arns = [
    "arn:aws:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-${var.environment}-summarization",
    "arn:aws:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-${var.environment}-notification",
    "arn:aws:lambda:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:function:${var.project_name}-${var.environment}-transcriber-batch-trigger",
  ]
}

# ---------------------------------------------------------------------------
# GPU instance profile (forward reference)
#
# gpu-spawner calls iam:PassRole on the IAM role that backs the GPU
# EC2 instance profile. That role lives in this same module (see
# `aws_iam_role.gpu_instance` in main.tf), so we reference it
# directly there rather than via a forward-constructed ARN.
# ---------------------------------------------------------------------------

# Account and region data sources used to construct ARNs for resources
# that do not yet have a dedicated Terraform module (Lambdas, the Batch
# job queue and definitions, the EventBridge bus). Building each ARN
# explicitly with the caller-identity account ID keeps these references
# identical across environments without hardcoding the account number.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Storage module remote state (consumed)
#
# Provides the transcripts bucket ARN + KMS CMK ARN. The
# WriteFinalTranscript Lambda role inherits write rights to the bucket
# from `dev/iam/`'s `transcriber-batch` task role; the Step Functions
# role itself only invokes Lambdas, so it does not need bucket-level
# rights. We still pull the bucket name into a local for documentation
# purposes (the Lambdas downstream read the same value).
# ---------------------------------------------------------------------------
data "terraform_remote_state" "storage" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/storage/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# IAM module remote state (consumed)
#
# Provides task-role ARNs that downstream modules reference when wiring
# the Lambdas this state machine invokes. We do not consume the role
# ARNs here directly (the state machine talks to function ARNs, not role
# ARNs), but we surface the iam state pin so a future change can
# tighten the InvokeFunction policy by Lambda function name without
# forcing a separate refactor.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "iam" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/iam/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Batch module remote state (forward reference)
#
# The `infra/dev/batch/` module that creates the GPU job queue and the
# `panakoes-dev-transcribe-batch` job definition does not exist yet
# (tracked in the backlog). When it lands, this `terraform_remote_state`
# block will resolve and the Map state's `JobQueue` / `JobDefinition`
# values will populate from its outputs. Until then the `try()` wrapper
# in `main.tf` falls back to constructed ARNs so this module remains
# self-applicable.
#
# `defaults = {}` is the documented escape hatch: when the underlying
# state file does not exist, terraform_remote_state would normally hard
# error during plan. With `defaults = {}` and `try()` reads against the
# `outputs` map, missing outputs degrade gracefully to the constructed
# fallback values.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "batch" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/batch/terraform.tfstate"
    region = "us-east-1"
  }

  defaults = {}
}

# ---------------------------------------------------------------------------
# Observability module remote state (forward reference)
#
# The `infra/dev/observability/` module that creates the shared dev KMS
# CMK used to encrypt CloudWatch log groups across services does not
# exist yet (tracked in the backlog). When it lands, we read the key
# ARN from its outputs. Until then we fall back to a fresh CMK
# provisioned in this module so the log group still encrypts under a
# customer-managed key from day one. The fallback key is small and
# self-contained; it can be retired by deleting it after the
# observability module is applied and the log group is reassigned.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "observability" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/observability/terraform.tfstate"
    region = "us-east-1"
  }

  defaults = {}
}

# ---------------------------------------------------------------------------
# KMS module remote state (Wave 2 consolidated CMKs, PR #365)
#
# W2-T4 extension: surfaces the consolidated `panakoes/logs` CMK ARN
# so the state-machine log group migrates onto the shared key. The
# module-local aws_kms_key.fallback_log resource is retained below
# (kept at count=1) for W2-T7 retirement (orchestrator-only step)
# but no longer encrypts the log group.
#
# This direct lookup also fixes the pre-existing bug in the
# `data.terraform_remote_state.observability` block above: that
# block reads `cloudwatch_logs_kms_key_arn` which is not an output
# of `infra/dev/observability/` (the real output is `kms_key_arn`).
# The bug was a no-op until now because the fallback CMK branch
# absorbed the null. With this change we bypass the wrong-name
# lookup entirely.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}

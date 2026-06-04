# Account / partition data sources used to build explicit ARNs for the
# Secrets Manager wildcard reference (the secrets module does not yet
# export the groq-api-key entry; we construct the ARN locally with the
# documented `-??????` suffix pattern).
data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

data "aws_region" "current" {}

# Storage module remote state: ARN + name of the audio-uploads bucket
# (so we can attach the EventBridge notification + grant the Lambda
# narrow s3:GetObject) and its CMK ARN (for kms:Decrypt).
data "terraform_remote_state" "storage" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/storage/terraform.tfstate"
    region = "us-east-1"
  }
}

# Data module remote state: ARN of the ingestion DynamoDB table for
# the worker's UpdateItem permission.
data "terraform_remote_state" "data" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/data/terraform.tfstate"
    region = "us-east-1"
  }
}

# ECR module remote state: the worker's container image lives in the
# panakoes-dev-transcribe-worker repository (added to ECR's service list
# in the same PR as this module). We need the repo URL for the Lambda's
# image_uri argument and the ECR CMK ARN so the Lambda can pull.
data "terraform_remote_state" "ecr" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/ecr/terraform.tfstate"
    region = "us-east-1"
  }
}

# IAM module remote state: the Lambda execution role lives in
# infra/dev/iam/ alongside every other service role, per the iam module
# convention. Pull its ARN here.
data "terraform_remote_state" "iam" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/iam/terraform.tfstate"
    region = "us-east-1"
  }
}

# Observability module remote state: pulls the shared CloudWatch Logs
# CMK so the Lambda's log group encrypts with the same key as the rest
# of the platform's log groups.
data "terraform_remote_state" "observability" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/observability/terraform.tfstate"
    region = "us-east-1"
  }
}

# KMS module remote state: the consolidated `alias/panakoes/app-data`
# and `alias/panakoes/logs` CMKs (W2-T7 re-point). The trigger queues
# move to app-data; the Lambda log group moves to logs.
data "terraform_remote_state" "kms" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/kms/terraform.tfstate"
    region = "us-east-1"
  }
}

# AWS Batch outputs (job queue + job definition) for the Whisper-on-GPU
# dispatch path. When TRANSCRIBER_BACKEND=batch, the Lambda submits jobs
# against these refs via batch:SubmitJob.
data "terraform_remote_state" "batch" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/batch/terraform.tfstate"
    region = "us-east-1"
  }
}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = data.aws_region.current.region

  audio_uploads_bucket_name = data.terraform_remote_state.storage.outputs.audio_uploads_bucket_name
  audio_uploads_bucket_arn  = data.terraform_remote_state.storage.outputs.audio_uploads_bucket_arn
  audio_uploads_kms_key_arn = data.terraform_remote_state.storage.outputs.audio_uploads_kms_key_arn

  ingestion_table_arn = data.terraform_remote_state.data.outputs.ingestion_table_arn

  ecr_repo_url = "${local.account_id}.dkr.ecr.${local.region}.amazonaws.com/${var.project_name}-${var.environment}-transcribe-worker"

  lambda_role_arn  = data.terraform_remote_state.iam.outputs.task_role_arns["transcribe-worker"]
  lambda_role_name = data.terraform_remote_state.iam.outputs.task_role_names["transcribe-worker"]

  # Same logs CMK as the rest of the platform's log groups.
  cloudwatch_logs_kms_key_arn = data.terraform_remote_state.observability.outputs.kms_key_arn

  # W2-T7 KMS consolidation re-point. The trigger queues move from the
  # per-service `panakoes-dev-transcribe-trigger` CMK to the consolidated
  # `alias/panakoes/app-data` key; the Lambda log group moves from the
  # per-service `panakoes-dev-transcribe-worker-log` CMK to the
  # consolidated `alias/panakoes/logs` key. The old per-service key
  # resources remain in this module for orchestrator-only retirement.
  app_data_kms_key_arn = data.terraform_remote_state.kms.outputs.app_data_key_arn
  logs_kms_key_arn     = data.terraform_remote_state.kms.outputs.logs_key_arn

  # Bare job-definition family ARN (no revision suffix). The batch module
  # exports `aws_batch_job_definition.transcribe.arn` which carries the
  # `:N` revision suffix, but SubmitJob accepts the bare family ARN to
  # target the latest revision; the runtime IAM policy needs to allow
  # both shapes (bare family + `:*` wildcard for any revision). Construct
  # by name rather than regex-stripping the revision because the family
  # name is stable while the revision integer changes on every redeploy.
  transcribe_job_def_family_arn = "arn:${local.partition}:batch:${local.region}:${local.account_id}:job-definition/${local.name_prefix}-transcribe-batch"
}

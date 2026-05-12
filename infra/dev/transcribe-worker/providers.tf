# Dev environment transcribe-worker providers.
#
# This config provisions the auto-transcription pipeline:
#   - SQS queue + DLQ that EventBridge fans S3 ObjectCreated events into.
#   - EventBridge rule on the default bus matching audio-uploads/audio/*.
#   - aws_s3_bucket_notification on the audio-uploads bucket enabling
#     EventBridge delivery (S3 only allows ONE such resource per bucket;
#     the storage module declares none today, so this owns it).
#   - Lambda function (container image), execution role, log group,
#     SQS event-source mapping.
#
# State is namespaced under `dev/transcribe-worker/` in the shared S3
# backend created by `infra/bootstrap/`.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/transcribe-worker/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "transcribe-worker"
      Service     = "transcription"
      Component   = "compute"
    }
  }
}

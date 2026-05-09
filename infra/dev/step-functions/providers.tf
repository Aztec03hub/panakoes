# Dev environment Step Functions providers.
#
# This config provisions the Standard-type Step Functions state machine
# that orchestrates the long-audio transcription pipeline (audio files
# whose duration exceeds the 600-second short-path threshold). Standard
# workflow type is mandatory here: Express workflows are capped at a
# five-minute total execution and bill on duration plus state transition
# count, neither of which fits a fan-out that may sit idle waiting for
# AWS Batch GPU jobs to drain. Standard executions persist for a year,
# emit a full audit trail to CloudWatch, and bill per state transition,
# which is the right shape for an orchestration that runs minutes-to-
# hours per execution.
#
# State is namespaced under `dev/step-functions/` in the shared S3
# backend created by `infra/bootstrap/`.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/step-functions/terraform.tfstate"
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
      Module      = "step-functions"
    }
  }
}

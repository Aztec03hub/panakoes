# Dev environment AWS Batch providers.
#
# This config provisions the AWS Batch GPU compute environment, job
# queue, and job definition that backs the Panakoes async transcription
# pipeline (Whisper-large-v3 fp16 on EC2 g4dn.xlarge Spot). Per
# CLAUDE.md the async transcriber runs on AWS Batch; this module is
# the infrastructure side of that decision.
#
# State is namespaced under `dev/batch/` in the shared S3 backend
# created by `infra/bootstrap/`. Backend values are hardcoded because
# Terraform does not allow variables in the backend block; they mirror
# the outputs of `infra/bootstrap/`.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket         = "panakoes-tf-state-b291597a"
    key            = "dev/batch/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    kms_key_id     = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    dynamodb_table = "panakoes-tf-lock"
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "dev-batch"
    }
  }
}

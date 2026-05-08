# Dev environment observability providers.
#
# This config creates the CloudWatch Log Groups, KMS CMK, S3 archive
# bucket, and supporting IAM role for the Panakoes `dev` environment's
# log shipping pipeline. Per-service log groups land at
# `/panakoes/dev/<service>` and stream into a long-term S3 archive that
# Athena can query later.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`. The `key` namespaces this configuration's state
# inside the bucket so other configs do not collide.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  backend "s3" {
    bucket         = "panakoes-tf-state-b291597a"
    key            = "dev/observability/terraform.tfstate"
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
      Module      = "observability"
    }
  }
}

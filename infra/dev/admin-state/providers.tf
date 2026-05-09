# Dev environment admin-state providers.
#
# This config creates the four DynamoDB tables that back the cost-api
# (Tier 2 of the admin dashboard) and admin-api (Tier 3 lifecycle
# controls) for the `dev` environment. State is namespaced under
# `dev/admin-state/` in the shared S3 backend created by
# `infra/bootstrap/`.
#
# These tables live in a separate Terraform configuration from
# `infra/dev/data/` for two reasons. First, blast-radius isolation:
# Tier 3 lifecycle operations are the most dangerous code path in the
# system, and the state backing them deserves its own apply boundary
# so a mis-applied Tier 3 schema change cannot accidentally damage
# the production-critical ingestion / audit / streaming-sessions
# tables. Second, the cost-api tables expand fastest as Phase 1 and
# Phase 2 land; isolating them avoids re-touching the data/ module's
# state on every iteration.
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
    bucket         = "panakoes-tf-state-b291597a"
    key            = "dev/admin-state/terraform.tfstate"
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
      Module      = "admin-state"
    }
  }
}

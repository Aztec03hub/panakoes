# Dev environment cost-rollup-aggregator providers.
#
# This config provisions the nightly Lambda + EventBridge Scheduler rule
# that populates `panakoes-dev-tenant-cost-rollup` from AWS Cost Explorer
# data. Without it, the cost-api `GET /api/v1/cost/by-tenant` route
# returns empty rows even when CE has spend data; this module is the
# missing populator.
#
# State is namespaced under `dev/cost-rollup-aggregator/` in the shared
# S3 backend created by `infra/bootstrap/`. Backend values are
# hardcoded because Terraform does not allow variables in the backend
# block; they mirror the outputs of `infra/bootstrap/`.

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
    key            = "dev/cost-rollup-aggregator/terraform.tfstate"
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
      Module      = "cost-rollup-aggregator"
    }
  }
}

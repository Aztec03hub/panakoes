# Dev environment cost-anomaly-monitor providers.
#
# This config provisions an AWS Cost Anomaly Detection monitor +
# subscription so the cost-api `GET /api/v1/cost/anomalies` endpoint
# returns real anomalies (instead of `[]`) once CE starts emitting them.
# State is namespaced under `dev/cost-anomaly-monitor/` in the shared S3
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
    key          = "dev/cost-anomaly-monitor/terraform.tfstate"
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
      Module      = "cost-anomaly-monitor"
    }
  }
}

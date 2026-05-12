# Dev environment AWS Budgets providers.
#
# Provisions a multi-threshold cost-guardrail layer for the Panakoes dev
# account: an account-wide monthly budget, four service-specific
# sub-budgets (EC2, Aurora, Bedrock, CloudFront + S3), and a tag-scoped
# per-environment budget keyed on `Project=panakoes`. Notifications flow
# through both direct EMAIL subscribers and a shared SNS topic so future
# Slack / PagerDuty fan-out is a topic-subscriber swap (not a budget
# resource change).
#
# AWS Budgets is a global service but the AWS provider still requires a
# region for its API calls; we keep it on us-east-1 to match every other
# Panakoes module.
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
    key          = "dev/budgets/terraform.tfstate"
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
      Module      = "budgets"
    }
  }
}

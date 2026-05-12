# Dev environment CloudFront WAFv2 providers.
#
# This config provisions the WAFv2 web ACL that fronts the dev
# SvelteKit admin CloudFront distribution
# (`panakoes-dev-admin`, host `dmaopcm3hnxog.cloudfront.net`).
#
# Why a second WAF module: AWS WAFv2 web ACLs are partitioned by
# `scope`. A `REGIONAL`-scope ACL (the one in `infra/dev/waf/`)
# can attach to ALB / API Gateway / AppSync only; CloudFront
# requires `scope = "CLOUDFRONT"` and the ACL MUST live in
# us-east-1 regardless of where the rest of the infrastructure
# runs. The two scopes cannot share an ACL, so we run two ACLs.
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
    key          = "dev/cloudfront-waf/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

# CloudFront-scoped WAFv2 web ACLs and their logging configuration
# must be created in us-east-1. The default region for the dev
# environment is already us-east-1, but we pin it explicitly here
# so a future region migration cannot silently break this module.
provider "aws" {
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = var.project_name
      Environment = var.environment
      ManagedBy   = "terraform"
      Module      = "cloudfront-waf"
    }
  }
}

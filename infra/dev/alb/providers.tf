# Dev environment shared internal Application Load Balancer providers.
#
# This module provisions the one internal ALB that replaces 11 internal
# NLBs (one per ECS service). The consolidation saves ~$182/mo:
# 11 NLBs at ~$18 each = $198 vs one ALB at ~$16.
#
# The ALB sits in the private subnets. It accepts traffic from the
# API Gateway VPC Link (sg-031e19cbcc6c33ea3, private-subnet ENIs) and
# forwards to ECS task IPs in the public subnets using path-based rules.
# ALBs route to target IPs via the VPC, not via the internet, so this
# topology is valid even though tasks are in public subnets.
#
# State is namespaced under `dev/alb/` in the shared S3 backend created
# by `infra/bootstrap/`.
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
    key          = "dev/alb/terraform.tfstate"
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
      Module      = "alb"
    }
  }
}

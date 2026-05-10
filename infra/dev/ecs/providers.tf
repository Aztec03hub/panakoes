# Dev environment ECS providers.
#
# This config provisions the dev-environment ECS Fargate cluster
# (`panakoes-dev`) plus the first application service deploy on top of
# it: the auth microservice (Better-Auth on Hono, TypeScript), its
# internal Network Load Balancer, target group, security group, task
# definition, and ECS service definition.
#
# This is the FIRST application-service deploy module in the project;
# every subsequent service module (ingestion-api, summarization,
# notification, query-api, session-manager, billing) follows the
# pattern set here. The `nlb_listener_arns` map output is the contract
# `infra/dev/api-gateway/` reads via `terraform_remote_state` to wire
# up its VPC Link integrations (see `data.tf` line 48-83 in the
# api-gateway module). Today the map has one entry (`auth`); the next
# service that ships appends its key.
#
# State namespace: `dev/ecs/`. Backend values are hardcoded because
# Terraform does not allow variables in the backend block. They mirror
# the outputs of `infra/bootstrap/`. `use_lockfile = true` uses S3's
# native lockfile feature; the legacy DynamoDB lock table was
# decommissioned in PR #180.

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
    key          = "dev/ecs/terraform.tfstate"
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
      Module      = "ecs"
    }
  }
}

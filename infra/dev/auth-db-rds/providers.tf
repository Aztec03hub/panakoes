# Dev environment auth-db (RDS PostgreSQL db.t4g.micro single-AZ) providers.
#
# This config provisions an RDS PostgreSQL 16 instance that backs the
# Better-Auth tables (`user`, `session`, `account`, `verification`) for
# the auth microservice in the `dev` environment.
#
# Migration context (2026-05-12):
# The auth-db originally lived on Aurora Serverless v2 (see
# `infra/dev/auth-db/`). Measurement showed that with `min_capacity_acu = 0`
# (the dev cost-saver setting), cold-start latency on first sign-in
# after the 5-minute idle window was ~11.6 seconds (Aurora resume from
# pause). For the auth workload (tiny storage, low sustained QPS, bursty
# at user login), Aurora Serverless v2's powers (auto-scale storage,
# multi-AZ replication, read replicas) are not used. RDS PostgreSQL
# db.t4g.micro single-AZ is the right shape: always-on, predictable
# latency, $0/mo for the first 12 months on AWS Free Tier, ~$12/mo
# after. See ADR-027 (forthcoming) for the full reasoning.
#
# State is namespaced under `dev/auth-db-rds/` in the shared S3 backend
# created by `infra/bootstrap/`. The old `dev/auth-db/` state continues
# to manage the Aurora cluster until the burn-in period ends and a
# follow-up PR decommissions it.
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
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  backend "s3" {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/auth-db-rds/terraform.tfstate"
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
      Module      = "auth-db-rds"
      Service     = "auth"
      Component   = "data"
    }
  }
}

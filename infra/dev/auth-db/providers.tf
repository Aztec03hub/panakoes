# Dev environment auth-db (Aurora Serverless v2 Postgres) providers.
#
# This config provisions the Aurora Serverless v2 Postgres 16 cluster
# that backs the Better-Auth tables (`user`, `session`, `account`,
# `verification`) for the auth microservice in the `dev` environment.
# Serverless v2 was chosen over a fixed-size Aurora cluster because dev
# traffic is bursty: scaling to 0.5 ACU during idle and up to 4 ACU
# under load keeps the bill in dollars-per-day instead of dollars-per-
# hour while still giving the auth service real Aurora behavior to
# integrate against. State is namespaced under `dev/auth-db/` in the
# shared S3 backend created by `infra/bootstrap/`.
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
    bucket         = "panakoes-tf-state-b291597a"
    key            = "dev/auth-db/terraform.tfstate"
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
      Module      = "auth-db"
    }
  }
}

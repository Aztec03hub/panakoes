# Dev environment IAM providers.
#
# This config provisions least-privilege IAM roles for every Panakoes
# microservice in the `dev` environment. Each service gets its own
# task role (the runtime identity the application code uses) and its
# own ECS task execution role (the identity the ECS agent uses to
# pull images, fetch secrets at startup, and ship logs). Service-only
# Lambdas like `event-router` and `gpu-spawner` use the task role as
# their Lambda execution role; the execution role concept does not
# apply to Lambda. State is namespaced under `dev/iam/` in the shared
# S3 backend created by `infra/bootstrap/`.
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
    key          = "dev/iam/terraform.tfstate"
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
      Module      = "iam"
      Service     = "platform"
      Component   = "security"
    }
  }
}

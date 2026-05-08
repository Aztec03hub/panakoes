# Dev environment network providers.
#
# This config creates the VPC and supporting networking primitives for
# the `dev` environment. It is the first per-environment Terraform
# configuration to consume the S3 remote state backend created by
# `infra/bootstrap/`.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`. The `key` namespaces this configuration's state
# inside the bucket so other configs (ECS, RDS, Lambda, Batch GPU) do
# not collide.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  backend "s3" {
    bucket         = "panakoes-tf-state-b291597a"
    key            = "dev/network/terraform.tfstate"
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
      Module      = "dev-network"
    }
  }
}

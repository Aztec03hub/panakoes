# Global module providers.
#
# This module manages account-wide, region-agnostic resources such as
# the GitHub Actions OIDC identity provider and the IAM role workflows
# assume. It uses the S3 remote state backend created by the bootstrap
# module; the bucket, KMS key, and DynamoDB lock table named below
# must already exist before `terraform init` will succeed.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`.

terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    bucket         = "panakoes-tf-state-b291597a"
    key            = "global/terraform.tfstate"
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
      Environment = "shared"
      ManagedBy   = "terraform"
      Module      = "global"
    }
  }
}

provider "tls" {}

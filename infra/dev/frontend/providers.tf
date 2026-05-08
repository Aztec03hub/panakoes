# Dev environment frontend providers.
#
# This config provisions the S3 origin bucket and the CloudFront
# distribution that serves the SvelteKit admin app for the Panakoes
# `dev` environment. The bucket is private (origin-access-control only),
# the distribution uses managed AWS-cache + security-headers policies,
# and a separate KMS-encrypted access-log bucket records every viewer
# request for audit + traffic analysis.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`. The `key` namespaces this configuration's state
# inside the bucket so other configs do not collide.

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
    key            = "dev/frontend/terraform.tfstate"
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
      Module      = "frontend"
    }
  }
}

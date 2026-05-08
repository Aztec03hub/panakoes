# Dev environment VPC endpoints providers.
#
# This config attaches VPC endpoints (gateway + interface) to the dev
# VPC's private subnets. Gateway endpoints are free and route S3 +
# DynamoDB traffic off the NAT (saving NAT data-processing charges).
# Interface endpoints add private DNS for AWS service APIs so traffic
# stays inside the VPC, eliminating NAT egress for ECR pulls, KMS
# decrypts, Secrets Manager reads, and the rest. Each interface
# endpoint costs about $0.01/hr per AZ; at three AZs that is roughly
# $22/month per service, which the NAT data-processing savings cover
# many times over once container images and CloudWatch Logs traffic
# starts flowing.
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
  }

  backend "s3" {
    bucket         = "panakoes-tf-state-b291597a"
    key            = "dev/vpc-endpoints/terraform.tfstate"
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
      Module      = "dev-vpc-endpoints"
    }
  }
}

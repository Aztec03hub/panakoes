# Dev environment WAFv2 providers.
#
# This config provisions the regional WAFv2 web ACL that fronts the
# public-facing Panakoes APIs (Ingestion API, Query API, Auth) once
# their ALBs / API Gateways exist. The ACL composes four AWS Managed
# Rule Groups (Common, Known Bad Inputs, IP Reputation, SQL Database),
# a per-IP rate-limit rule, and a commented geo-block placeholder. WAF
# logs are shipped to a dedicated KMS-encrypted CloudWatch log group.
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
    bucket         = "panakoes-tf-state-b291597a"
    key            = "dev/waf/terraform.tfstate"
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
      Module      = "waf"
    }
  }
}

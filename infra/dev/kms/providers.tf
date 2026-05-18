# Dev environment consolidated-KMS providers.
#
# This module provisions the two consolidated Customer Managed Keys
# (CMKs) introduced by Wave 2 of the infrastructure migration
# (ARCH-MIGRATION.md section 2.2):
#
#   - `alias/panakoes/app-data` for S3, RDS, SQS, SNS, Secrets Manager,
#     ECR, Backup vault, frontend S3, and security log buckets
#   - `alias/panakoes/logs`     for CloudWatch Logs groups across services
#
# These two keys replace 15 of the existing 19 service-specific CMKs
# (12 collapse onto `app-data`, 5 onto `logs`). Two CMKs remain separate
# by design and are NOT managed here:
#
#   - `alias/panakoes-dev-jwt-signing` (asymmetric RSA_2048; lives in
#     `infra/dev/auth-kms-signing/`). Kept separate so a compromise of
#     app-data encryption cannot enable JWT forgery.
#   - `alias/panakoes-tf-state` (lives in `infra/bootstrap/`). Kept
#     separate so a compromise of any single in-band key cannot also
#     expose Terraform state, which encodes the rest of the account.
#
# Scope of THIS module's apply: create the 2 new keys and 2 new aliases
# and nothing else. The actual migration of consumers (S3 bucket
# encryption configs, RDS storage key, Secrets Manager secrets, SQS / SNS
# / ECR / Backup, CloudWatch log groups) happens in follow-up PRs
# W2-T2 through W2-T7. Those follow-up PRs read the outputs of this
# module via `terraform_remote_state`.
#
# State is namespaced under `dev/kms/` in the shared S3 backend created
# by `infra/bootstrap/`. Backend values are hardcoded because Terraform
# does not allow variables in the backend block; they mirror the
# bootstrap outputs.

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
    key          = "dev/kms/terraform.tfstate"
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
      Module      = "kms"
    }
  }
}

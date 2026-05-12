# Dev environment security-services providers.
#
# This config defines the dev environment security observability
# stack: AWS Config (resource configuration recorder + managed
# rules), Amazon GuardDuty (threat detection), and AWS Security
# Hub (cross-service findings aggregator).
#
# IMPORTANT: every paid component is plan-clean by default. The
# variables `enable_config`, `enable_guardduty`, and
# `enable_security_hub` all default to `false`. Flipping each to
# `true` is a deliberate post-apply step (see README) so an
# unattended `terraform apply` cannot start incurring rule
# evaluation, finding aggregation, or threat-detection charges.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`. The `key` namespaces this configuration's
# state inside the bucket so other configs do not collide.

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
    key          = "dev/security/terraform.tfstate"
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
      Module      = "security"
      Service     = "platform"
      Component   = "security"
    }
  }
}

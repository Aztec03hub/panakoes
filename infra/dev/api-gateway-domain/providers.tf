# Dev environment API Gateway custom-domain providers.
#
# This config provisions the ACM certificate, API Gateway v2 custom
# domain name, and API mapping that front the dev HTTP API at
# `api.dev.panakoes.com`. DNS for `panakoes.com` is authoritative on
# Cloudflare (registered 2026-05-07); both the ACM DNS-validation
# CNAME and the user-facing CNAME are added by hand to Cloudflare
# after the relevant resources land. The cert MUST live in us-east-1
# because API Gateway v2 (HTTP API) requires the ACM certificate in
# the same region as the API itself (this differs from CloudFront,
# which always pulls from us-east-1).
#
# Why split this out of `dev/api-gateway/`: the certificate apply has
# a human-in-the-loop wait (Cloudflare DNS records) and the cert can
# sit in PENDING_VALIDATION for 5 to 30 minutes. Isolating it lets the
# main api-gateway module continue to apply cleanly without dragging
# the validation wait into every plan.
#
# State namespace: `dev/api-gateway-domain/`. Backend values hardcoded
# because Terraform does not permit variables in the backend block.
# Values mirror `infra/bootstrap/`.

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
    key          = "dev/api-gateway-domain/terraform.tfstate"
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
      Module      = "api-gateway-domain"
    }
  }
}

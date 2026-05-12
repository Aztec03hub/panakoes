# Dev environment auth-kms-signing providers.
#
# This config provisions the AWS KMS asymmetric key the auth service
# uses to sign RS256 JWTs (ADR-041 phase 1). The private key material
# lives entirely inside KMS; the service signs by calling `kms:Sign`
# and never sees the key bytes. The public key is exposed at the auth
# service's `/.well-known/jwks.json` endpoint via `kms:GetPublicKey`.
#
# Why a separate Terraform state file from `infra/dev/secrets/`:
# the asymmetric KMS key is a one-resource addition with a distinct
# blast radius (deleting it breaks JWT verification across every
# downstream service) and a distinct lifecycle (the key is meant to
# rotate manually via documented procedure, not via the shared
# `infra/dev/secrets/` apply cadence). A dedicated module lets us
# `terraform plan -target` it in isolation when migrating the cluster
# to RS256.
#
# State is namespaced under `dev/auth-kms-signing/` in the shared S3
# backend created by `infra/bootstrap/`.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  backend "s3" {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "dev/auth-kms-signing/terraform.tfstate"
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
      Module      = "auth-kms-signing"
    }
  }
}

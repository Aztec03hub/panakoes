# Cloudflare DNS module providers.
#
# This module manages DNS records on the two Cloudflare zones owned
# by LaFayette Labs: `panakoes.com` (project) and `lafayettelabs.com`
# (LLC). It uses the S3 remote state backend created by the bootstrap
# module so state is encrypted-at-rest with the same CMK that protects
# every other infra/ module's state.
#
# Backend values are hardcoded because Terraform does not allow
# variables in the backend block. They mirror the outputs of
# `infra/bootstrap/`. The state key is namespaced under `global/` so
# this module's state sits alongside the existing global config.
#
# The Cloudflare API token is NOT a Terraform variable that ships in
# the repo. It is read from `var.cloudflare_api_token`, populated via
# `TF_VAR_cloudflare_api_token=...` env var on the operator's local
# machine (or via an operator-local, gitignored `terraform.tfvars`).
# See README.md for the token-scope spec.

terraform {
  required_version = ">= 1.7"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }

  backend "s3" {
    bucket       = "panakoes-tf-state-b291597a"
    key          = "global/cloudflare-dns/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    kms_key_id   = "arn:aws:kms:us-east-1:659225405128:key/dce57db1-ea8c-46dd-b60a-c8de022860af"
    use_lockfile = true
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}

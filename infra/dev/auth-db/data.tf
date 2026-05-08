# Account and region data sources used to construct ARNs and to surface
# the caller-identity-pinned KMS key policy for the cluster CMK.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Network module remote state (consumed)
#
# Provides the dev VPC ID, the three private subnet IDs (us-east-1a/b/c)
# the DB subnet group spans, and the VPC CIDR block (`10.10.0.0/16`)
# that gates the cluster security group's inbound 5432/TCP rule. The
# private subnets are the right home for an Aurora cluster: only
# resources inside the VPC (ECS tasks, Lambda in the VPC, bastion via
# Session Manager) can reach the writer endpoint.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "network" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/network/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Secrets module remote state (consumed; soft-referenced)
#
# The Secrets Manager `panakoes-dev/postgres-auth-db-password` secret is
# the canonical location for the master password. When the secrets
# module is applied, this remote-state read resolves and `try()` in
# `main.tf` picks up the secret ARN; we then pull the secret value via
# `aws_secretsmanager_secret_version`. Until secrets is applied (or in
# a plan-only pipeline that runs before secrets), `defaults = {}` plus
# `try()` lets the cluster fall back to a `random_password` resource so
# `terraform plan` and `terraform validate` still succeed standalone.
#
# Plan-only fallback expectations:
#   - terraform validate / plan succeeds against a brand-new state.
#   - On real apply, secrets is applied first; the cluster then reads
#     the real password from Secrets Manager and the `random_password`
#     resource is unused (Terraform will drop it from state on the
#     next refactor). Documented in this module's README.
# ---------------------------------------------------------------------------
data "terraform_remote_state" "secrets" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/secrets/terraform.tfstate"
    region = "us-east-1"
  }

  defaults = {}
}

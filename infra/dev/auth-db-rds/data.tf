# Account and region data sources used to construct ARNs and to surface
# the caller-identity-pinned KMS key policy for the instance CMK.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# ---------------------------------------------------------------------------
# Network module remote state (consumed)
#
# Provides the dev VPC ID, the three private subnet IDs (us-east-1a/b/c)
# the DB subnet group spans, and the VPC CIDR block (`10.10.0.0/16`)
# that gates the instance security group's inbound 5432/TCP rule. The
# instance lives in private subnets only; there is no publicly-accessible
# RDS in any Panakoes environment.
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
# The Secrets Manager `panakoes-dev/postgres-auth-db-password` secret
# is the canonical location for the master password. Same pattern as
# the Aurora module it replaces: when the secrets module is applied,
# this remote-state read resolves and `try()` in `main.tf` picks up
# the secret ARN; we then pull the secret value via
# `aws_secretsmanager_secret_version`. Until secrets is applied (or
# in a plan-only pipeline that runs before secrets), `defaults = {}`
# plus `try()` lets the instance fall back to a `random_password`
# resource so `terraform plan` and `terraform validate` still succeed
# standalone.
#
# Note: the migration cutover (writing the new RDS DSN back into
# Secrets Manager) is done out-of-band via the AWS CLI, not via
# Terraform. The cutover sequence is documented in the README.
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

# W2-T5 (DEFERRED): the planned `data "terraform_remote_state" "kms"`
# lookup against `dev/kms/terraform.tfstate` was removed when this
# task was escalated out of the W2-T2..T6 bundle. Reason: flipping
# `aws_db_instance.kms_key_id` is a ForceNew attribute that would
# destroy and recreate the live auth-db instance, losing the user /
# session tables on the volume. Migration to the consolidated
# `panakoes/app-data` CMK requires an out-of-band snapshot ->
# restore-into-new-instance-with-new-CMK -> DNS swap sequence rather
# than a single Terraform apply. The follow-up agent will reintroduce
# the lookup at the same time it adds the snapshot+restore plumbing.

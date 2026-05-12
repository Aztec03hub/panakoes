# Account and region data sources used to build explicit ARNs for
# resources that do not yet have their own Terraform module (today the
# system-alerts SNS topic). Building the ARN with caller-identity keeps
# the config identical across environments without hardcoding the
# account number in source.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

# Data module remote state: DynamoDB tables (ingestion, audit log,
# streaming sessions). We list the table ARNs explicitly in the backup
# selection's `resources` list rather than relying on a tag-based
# selection alone, so the protection takes effect immediately on apply
# even before the `Backup = enabled` tag is added to each table in
# `infra/dev/data/`.
data "terraform_remote_state" "data" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/data/terraform.tfstate"
    region = "us-east-1"
  }
}

# Auth-db module remote state: Aurora Serverless v2 cluster
# (`panakoes-dev-auth-*`). The cluster ARN is listed alongside the
# DynamoDB tables in the backup selection's `resources` list so AWS
# Backup takes daily cluster snapshots into the vault on top of the
# native 7-day Aurora PITR window. The PR-282 restore drill validated
# native PITR; this module closes the AWS-Backup-vault gap that drill
# surfaced (zero Aurora recovery points in the vault).
data "terraform_remote_state" "auth_db" {
  backend = "s3"

  config = {
    bucket = "panakoes-tf-state-b291597a"
    key    = "dev/auth-db/terraform.tfstate"
    region = "us-east-1"
  }
}

# ---------------------------------------------------------------------------
# Vault notifications target SNS topic (forward reference)
#
# A dedicated `infra/dev/events/` Terraform module that owns the
# `panakoes-dev-system-alerts` SNS topic does not yet exist. Until it
# does, we construct the topic ARN explicitly with the documented SNS
# pattern:
#
#   arn:aws:sns:<region>:<account-id>:<topic-name>
#
# This keeps `aws_backup_vault_notifications` deployable today; when
# `infra/dev/events/` lands, swap the local for a
# `terraform_remote_state` lookup and the resource definition stays
# identical. Note that the topic does NOT need to exist at apply time
# for `aws_backup_vault_notifications` to be created (AWS Backup
# validates the ARN format, not topic existence at creation time), but
# notifications will only be delivered once the topic is provisioned.
#
# TODO: replace this local with a `terraform_remote_state` lookup
# against `dev/events/terraform.tfstate` once that module exists.
# ---------------------------------------------------------------------------
locals {
  system_alerts_topic_arn = "arn:aws:sns:${data.aws_region.current.region}:${data.aws_caller_identity.current.account_id}:${var.project_name}-${var.environment}-system-alerts"
}

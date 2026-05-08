locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "backup"
  }

  name_prefix = "${var.project_name}-${var.environment}"

  # DynamoDB tables protected by this backup plan. Listed by ARN so
  # the selection takes effect on apply without depending on the
  # `Backup = enabled` tag (which lands in a follow-up PR against
  # `infra/dev/data/`). Adding a new table to the protected set is a
  # single-line change here once the table ARN is exported from the
  # data module.
  protected_table_arns = [
    data.terraform_remote_state.data.outputs.ingestion_table_arn,
    data.terraform_remote_state.data.outputs.audit_log_table_arn,
    data.terraform_remote_state.data.outputs.streaming_sessions_table_arn,
  ]
}

# ---------------------------------------------------------------------------
# Customer-managed KMS key
#
# A dedicated CMK encrypts every recovery point in the dev backup
# vault. AWS Backup requires a KMS key (default-encrypted vaults use
# the AWS-owned `aws/backup` key, but a CMK gives us key-policy and
# audit control we want from day one).
#
# Rotation enabled (annual, AWS-managed). Deletion window is 7 days
# (matching `infra/dev/secrets/` and deliberately shorter than the
# 30-day default used by S3 / DynamoDB CMKs in sibling modules) so a
# fat-finger destroy on the dev backup stack does not strand the team
# for a month. A 7-day window is safe in dev because real recovery
# data sits in DynamoDB PITR independently; the AWS Backup vault is a
# secondary, point-in-time recovery target, not the only line of
# defense.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "backup" {
  description             = "KMS key for the dev AWS Backup vault"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  tags = local.common_tags
}

resource "aws_kms_alias" "backup" {
  name          = "alias/${local.name_prefix}-backup"
  target_key_id = aws_kms_key.backup.key_id
}

# ---------------------------------------------------------------------------
# Backup vault
#
# Single vault holds all dev recovery points. AWS Backup vault names
# share a flat namespace per region, so the `panakoes-dev` prefix
# disambiguates from any future `panakoes-staging` / `panakoes-prod`
# vaults. KMS-encrypted with the dedicated CMK above.
#
# `force_destroy = false` (the default) is intentional: destroying the
# vault while recovery points exist is exactly the kind of mistake we
# want Terraform to refuse. To wipe and recreate, run
# `aws backup delete-recovery-point` per recovery point first, then
# `terraform destroy`.
# ---------------------------------------------------------------------------

resource "aws_backup_vault" "dev" {
  name        = local.name_prefix
  kms_key_arn = aws_kms_key.backup.arn

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Backup plan
#
# Two rules: a frequent short-retention daily and an infrequent
# long-retention monthly. The daily rule covers the "oh no I just
# corrupted the table" recovery window; the monthly rule covers the
# "we need to audit what the data looked like 9 months ago" window.
#
# Schedules are UTC and use AWS Backup's six-field cron format:
#   cron(minute hour day-of-month month day-of-week year)
# Day-of-month and day-of-week are mutually exclusive; the unused
# field is `?`.
#
# Daily rule:
#   - cron(0 5 ? * * *) fires at 05:00 UTC every day.
#   - 5-hour `start_window` and 7-hour `completion_window` give AWS
#     Backup elasticity to absorb queueing without missing the SLA.
#     5h is the AWS-recommended minimum for the start window.
#   - 30-day retention keeps recent-enough copies for fast restore
#     without ballooning storage cost.
#
# Monthly rule:
#   - cron(0 5 1 * ? *) fires at 05:00 UTC on the 1st of each month.
#   - 365-day retention covers a year of compliance and forensic
#     queries. Cold-storage tiering is not enabled at the dev tier
#     because the per-table data volume is tiny and warm storage is
#     cheap enough that the 90-day minimum cold-storage-before-restore
#     friction is not worth the savings.
#
# `copy_action` is intentionally omitted on both rules: dev does not
# replicate to a second region or account. Production should add a
# cross-region copy and cross-account copy for blast-radius isolation.
# ---------------------------------------------------------------------------

resource "aws_backup_plan" "dev" {
  name = "${local.name_prefix}-daily-monthly"

  rule {
    rule_name         = "daily-30d-retention"
    target_vault_name = aws_backup_vault.dev.name
    schedule          = "cron(0 5 ? * * *)"

    start_window      = 300 # minutes (5 hours)
    completion_window = 420 # minutes (7 hours)

    lifecycle {
      delete_after = 30
    }
  }

  rule {
    rule_name         = "monthly-365d-retention"
    target_vault_name = aws_backup_vault.dev.name
    schedule          = "cron(0 5 1 * ? *)"

    start_window      = 300
    completion_window = 420

    lifecycle {
      delete_after = 365
    }
  }

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Backup IAM service role
#
# AWS Backup assumes this role to take backups (and later restore from
# them) of the resources listed in the selection. The two AWS-managed
# policies cover the standard read-tables / write-recovery-points and
# create-restore-targets flows. We attach both even though the dev
# stack only takes backups today, because adding the restore policy
# costs nothing and unblocks ad-hoc recovery testing without a second
# Terraform apply.
#
# AWSBackupServiceRolePolicyForBackup grants the actions AWS Backup
# needs to read source resources (DynamoDB DescribeTable / Scan, EBS
# CreateSnapshot, RDS CreateDBSnapshot, etc.) and write recovery
# points.
#
# AWSBackupServiceRolePolicyForRestores grants the actions AWS Backup
# needs to recreate resources from recovery points (CreateTable, and
# the analogous restore actions for other resource types).
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "backup_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["backup.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "backup" {
  name                 = "${local.name_prefix}-backup"
  description          = "AWS Backup service role for the ${var.environment} environment"
  assume_role_policy   = data.aws_iam_policy_document.backup_trust.json
  max_session_duration = 3600

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "backup_managed" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup"
}

resource "aws_iam_role_policy_attachment" "restore_managed" {
  role       = aws_iam_role.backup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForRestores"
}

# ---------------------------------------------------------------------------
# Backup selection
#
# Lists the protected resources by ARN so backups start the moment
# this module is applied, independent of any tag plumbing. The
# `selection_tag` block ALSO selects on the `Backup = enabled` tag,
# which is the longer-term mechanism: as the data module grows, new
# tables tagged `Backup = enabled` are picked up automatically without
# editing this file.
#
# AWS Backup unions the explicit `resources` list with the
# tag-selection criteria, so a resource matched by either path is
# included. Listing the current dev tables explicitly also doubles as
# documentation: anyone reading this module sees exactly which tables
# are protected today.
# ---------------------------------------------------------------------------

resource "aws_backup_selection" "dev" {
  iam_role_arn = aws_iam_role.backup.arn
  name         = "${local.name_prefix}-dynamodb"
  plan_id      = aws_backup_plan.dev.id

  resources = local.protected_table_arns

  selection_tag {
    type  = "STRINGEQUALS"
    key   = "Backup"
    value = "enabled"
  }
}

# ---------------------------------------------------------------------------
# Vault notifications
#
# Publish key vault lifecycle events to the project's system-alerts
# SNS topic. The events we care about (job failures, expired recovery
# points, restore failures) are a strict subset of the available event
# types; the unsubscribed events are noisy and not actionable.
#
# The target SNS topic ARN is constructed in `data.tf` because the
# `infra/dev/events/` module that will own the topic does not yet
# exist. AWS Backup accepts the resource definition without the topic
# existing (the API validates ARN format, not target existence), but
# notifications only flow once the topic is provisioned and a Backup
# service-principal SNS publish policy is attached. Both follow-ups
# are tracked in the events-module backlog item.
# ---------------------------------------------------------------------------

resource "aws_backup_vault_notifications" "dev" {
  backup_vault_name = aws_backup_vault.dev.name
  sns_topic_arn     = local.system_alerts_topic_arn
  backup_vault_events = [
    "BACKUP_JOB_FAILED",
    "BACKUP_JOB_EXPIRED",
    "RESTORE_JOB_FAILED",
    "COPY_JOB_FAILED",
    "RECOVERY_POINT_MODIFIED",
  ]
}

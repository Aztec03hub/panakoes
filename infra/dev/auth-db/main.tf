locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "auth-db"
  }

  name_prefix = "${var.project_name}-${var.environment}"
  cluster_id  = "${local.name_prefix}-auth"

  # Network outputs pulled into short locals.
  vpc_id             = data.terraform_remote_state.network.outputs.vpc_id
  vpc_cidr_block     = data.terraform_remote_state.network.outputs.vpc_cidr_block
  private_subnet_ids = data.terraform_remote_state.network.outputs.private_subnet_ids

  # ---------------------------------------------------------------------------
  # Master password resolution
  #
  # Preferred path: read the `panakoes-dev/postgres-auth-db-password`
  # secret out of `infra/dev/secrets/`. If that module has not yet been
  # applied, the `try()` falls through to a Terraform-managed
  # `random_password` so plans on a green field still succeed. The
  # password the cluster ends up with on first apply is whichever value
  # the chain resolves to; the README's post-apply step rotates the
  # master password explicitly via `aws rds modify-db-cluster`, so the
  # first-apply value is intentionally a one-shot bootstrap, not a
  # long-lived credential.
  # ---------------------------------------------------------------------------
  postgres_password_secret_arn = try(
    data.terraform_remote_state.secrets.outputs.secret_arns["postgres-auth-db-password"],
    null,
  )

  master_password = (
    local.postgres_password_secret_arn != null
    ? data.aws_secretsmanager_secret_version.master_password[0].secret_string
    : random_password.master_password.result
  )
}

# ---------------------------------------------------------------------------
# Customer-managed KMS key (CMK) for the cluster
#
# Encrypts the cluster's storage volumes and Performance Insights data
# under a key Panakoes owns rather than the AWS-managed `aws/rds`
# default. Trade-offs documented for interview-defensibility:
#
#   - With the default `aws/rds` key, AWS controls rotation, key policy,
#     and whether the key can be disabled. The cluster cannot be
#     decrypted by anything outside the account, but rotation cadence
#     is opaque.
#   - With a customer-managed CMK, we own rotation (annual,
#     AWS-managed when `enable_key_rotation = true`), can scope key
#     usage with conditions, and can disable the key in an incident to
#     immediately freeze access to the cluster's snapshots and
#     replicas.
#
# 7-day deletion window matches the sibling `dev/secrets/` module
# rationale: dev material is replaceable in minutes; long undelete
# windows block reuse of the alias name. Production should bump this
# to the 30-day default.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "auth_db" {
  description             = "KMS key for the dev Aurora Serverless v2 auth-db cluster (storage + Performance Insights encryption)"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  tags = local.common_tags
}

resource "aws_kms_alias" "auth_db" {
  name          = "alias/${local.name_prefix}-auth-db"
  target_key_id = aws_kms_key.auth_db.key_id
}

# ---------------------------------------------------------------------------
# DB subnet group
#
# Aurora requires a subnet group covering at least two AZs. We use all
# three private subnets from `dev/network/` so the cluster can be
# scheduled in any of us-east-1a/b/c if AWS pulls capacity from one
# AZ. The cluster lives in private subnets only; there is no
# publicly-accessible Aurora in any Panakoes environment.
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "auth_db" {
  name        = "${local.name_prefix}-auth-db"
  description = "Subnet group spanning the three dev private subnets for the Aurora auth-db cluster"
  subnet_ids  = local.private_subnet_ids

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Cluster security group
#
# Allow 5432/TCP inbound from the VPC CIDR (`10.10.0.0/16`) and nothing
# else. This is wider than per-service allow-listing (which would
# require an ECS task SG ID up front), but keeps the cluster reachable
# from any ECS task, Lambda-in-VPC, or operator session attached to
# this VPC without requiring a follow-up Terraform apply every time a
# new consumer lands. Egress is locked open to anywhere; Aurora has no
# meaningful outbound surface, but the AWS default allow-all egress
# rule has to be explicit for least-surprise.
# ---------------------------------------------------------------------------

resource "aws_security_group" "auth_db" {
  name        = "${local.name_prefix}-auth-db-sg"
  description = "Allow 5432/TCP inbound to the dev Aurora auth-db cluster from the dev VPC CIDR"
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-auth-db-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "auth_db_postgres_from_vpc" {
  security_group_id = aws_security_group.auth_db.id
  description       = "Postgres 5432/TCP from inside the dev VPC"
  cidr_ipv4         = local.vpc_cidr_block
  ip_protocol       = "tcp"
  from_port         = 5432
  to_port           = 5432

  tags = local.common_tags
}

resource "aws_vpc_security_group_egress_rule" "auth_db_egress_all" {
  security_group_id = aws_security_group.auth_db.id
  description       = "Egress is unrestricted; Aurora has no meaningful outbound surface, but the rule is explicit for least-surprise"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Master password resolution helpers
#
# `random_password` is the plan-clean fallback when the secrets module
# has not yet been applied. Length 32, no special characters that
# Aurora's master-user shell-escapes treat oddly. On first apply the
# README walks Phil through rotating to a Secrets-Manager-stored value
# via `aws rds modify-db-cluster`, after which `random_password`
# becomes vestigial state.
# ---------------------------------------------------------------------------

resource "random_password" "master_password" {
  length      = 32
  special     = true
  min_lower   = 4
  min_upper   = 4
  min_numeric = 4
  min_special = 2

  # Avoid characters that the Postgres URL grammar treats as separators
  # or that AWS rejects in master-user passwords (slash, at-sign,
  # double-quote, single-quote, backtick, space).
  override_special = "!#$%&*+,-.:;<=>?[]^_{|}~"
}

# When the secrets module has been applied, pull the live secret value.
# Count is 1 when the ARN is known, 0 when the secrets module is still
# absent; the consumer in `local.master_password` reads `[0]` only when
# the count is 1.
data "aws_secretsmanager_secret_version" "master_password" {
  count     = local.postgres_password_secret_arn != null ? 1 : 0
  secret_id = local.postgres_password_secret_arn
}

# ---------------------------------------------------------------------------
# Aurora Serverless v2 cluster
#
# Why Serverless v2 over a fixed-size cluster:
#   - Dev traffic is bursty. Idle scaling to 0.5 ACU is roughly $0.06/hr
#     vs about $0.10/hr for the smallest fixed instance (db.t4g.medium).
#   - Scale-up under integration-test load is automatic and second-ish
#     per ACU step, no provisioning lag.
#
# Why Aurora over plain RDS Postgres:
#   - Better-Auth's `session` table is a hot read path. Aurora's storage
#     replication latency and read-replica failover semantics give us
#     the "make it work in prod" path without re-platforming.
#   - Performance Insights and Enhanced Monitoring tooling parity with
#     the production cluster keeps observability investment portable.
#
# Backup retention 7 days matches the dev tier; production bumps to 30.
# Deletion protection ON because losing a Better-Auth user table would
# log every user out of every dev environment and force a replay of
# any dev test data accumulated over the week. `skip_final_snapshot`
# is true so destroying the dev cluster does not block on a snapshot
# we will not consume; production must flip this to false.
# ---------------------------------------------------------------------------

resource "aws_rds_cluster" "auth_db" {
  cluster_identifier_prefix = "${local.cluster_id}-"
  engine                    = "aurora-postgresql"
  engine_mode               = "provisioned"
  engine_version            = var.engine_version
  database_name             = var.database_name
  master_username           = var.master_username
  master_password           = local.master_password
  port                      = 5432

  db_subnet_group_name   = aws_db_subnet_group.auth_db.name
  vpc_security_group_ids = [aws_security_group.auth_db.id]

  storage_encrypted = true
  kms_key_id        = aws_kms_key.auth_db.arn

  backup_retention_period = var.backup_retention_days
  preferred_backup_window = "03:00-04:00" # UTC; pre-dawn US-east window

  deletion_protection = true
  skip_final_snapshot = true # dev only; production sets false + final_snapshot_identifier

  apply_immediately = true

  serverlessv2_scaling_configuration {
    min_capacity = var.min_capacity_acu
    max_capacity = var.max_capacity_acu
  }

  tags = merge(local.common_tags, {
    Name = local.cluster_id
  })

  lifecycle {
    # The master_password is rotated post-apply via the AWS CLI (see
    # README); ignoring the value here lets that rotation persist
    # without Terraform reverting it on the next apply.
    ignore_changes = [master_password]
  }
}

# ---------------------------------------------------------------------------
# Enhanced Monitoring IAM role
#
# RDS Enhanced Monitoring streams OS-level metrics (CPU, memory, disk,
# network) to CloudWatch Logs every N seconds. Without this role,
# instance-level CloudWatch metrics stop at the basic 1-minute
# aggregates the RDS service publishes itself. The trust policy lets
# the `monitoring.rds.amazonaws.com` service principal assume the
# role; the AWS-managed `AmazonRDSEnhancedMonitoringRole` policy is
# the AWS-recommended permission surface (write-only into a
# CloudWatch Logs group RDS itself manages).
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "rds_monitoring_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["monitoring.rds.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "rds_enhanced_monitoring" {
  name               = "${local.name_prefix}-auth-db-enhanced-monitoring"
  description        = "RDS Enhanced Monitoring role for the dev auth-db Aurora cluster"
  assume_role_policy = data.aws_iam_policy_document.rds_monitoring_trust.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "rds_enhanced_monitoring" {
  role       = aws_iam_role.rds_enhanced_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ---------------------------------------------------------------------------
# Writer instance
#
# One Serverless v2 writer in dev. Production should add a second
# instance in a different AZ for HA failover; the dev tier accepts the
# single-AZ outage risk in exchange for half the cost. instance_class
# `db.serverless` is the only valid class for Serverless v2; the
# capacity range comes from the cluster's
# `serverlessv2_scaling_configuration` block above.
#
# Performance Insights uses the same CMK as cluster storage so a single
# kms:Decrypt grant covers both surfaces in any consumer IAM policy.
# 7-day retention is the free tier; longer retention crosses into the
# paid Performance Insights tier and is unnecessary for dev.
#
# Enhanced Monitoring at 60-second granularity gives us per-minute
# OS metrics; lower intervals (1, 5, 10, 15, 30 seconds) increase the
# log-stream cost without buying us anything for a dev cluster.
# ---------------------------------------------------------------------------

resource "aws_rds_cluster_instance" "auth_db_writer" {
  identifier         = "${local.cluster_id}-writer"
  cluster_identifier = aws_rds_cluster.auth_db.id
  engine             = aws_rds_cluster.auth_db.engine
  engine_version     = aws_rds_cluster.auth_db.engine_version
  instance_class     = "db.serverless"

  db_subnet_group_name = aws_db_subnet_group.auth_db.name

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.auth_db.arn
  performance_insights_retention_period = 7

  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_enhanced_monitoring.arn

  auto_minor_version_upgrade = true
  publicly_accessible        = false

  tags = merge(local.common_tags, {
    Name = "${local.cluster_id}-writer"
  })
}

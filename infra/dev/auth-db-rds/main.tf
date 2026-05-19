locals {
  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
    Module      = "auth-db-rds"
  }

  name_prefix = "${var.project_name}-${var.environment}"
  instance_id = "${local.name_prefix}-auth-rds"

  # Network outputs pulled into short locals.
  vpc_id             = data.terraform_remote_state.network.outputs.vpc_id
  vpc_cidr_block     = data.terraform_remote_state.network.outputs.vpc_cidr_block
  private_subnet_ids = data.terraform_remote_state.network.outputs.private_subnet_ids

  # ---------------------------------------------------------------------------
  # Master password resolution
  #
  # Same chain as the Aurora module: prefer the Secrets Manager
  # `panakoes-dev/postgres-auth-db-password` secret when secrets has
  # been applied, otherwise fall back to a Terraform-managed
  # `random_password` so plans on a green field succeed. The README's
  # post-apply step rotates the master password explicitly via
  # `aws rds modify-db-instance`; the first-apply value is a one-shot
  # bootstrap.
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

  # W2-T5 (follow-up): the consolidated panakoes/app-data CMK ARN, used
  # to re-encrypt the snapshot copy AND the v2 instance below. The
  # original `aws_db_instance.auth_db` is intentionally NOT migrated
  # because `kms_key_id` is ForceNew and would destroy + recreate the
  # live instance, losing the Better-Auth user/session tables.
  app_data_kms_key_arn = data.terraform_remote_state.kms.outputs.app_data_key_arn

  # Identifier for the v2 instance (snapshot-restored, encrypted under
  # the consolidated CMK). Distinct from `local.instance_id` so the
  # two instances coexist during the burn-in window before the v1
  # retirement PR (W2-T7) deletes the original.
  instance_id_v2 = "${local.name_prefix}-auth-rds-v2"

  # One-shot identifiers for the pre-migration snapshot and its
  # re-encrypted copy. Using a fixed suffix (rather than a timestamp)
  # keeps the resources idempotent across plans; the snapshot is taken
  # exactly once per Terraform-managed migration and re-applies of this
  # module are no-ops on these two resources.
  pre_migration_snapshot_id = "${local.instance_id}-pre-w2-t5-migration"
  re_encrypted_snapshot_id  = "${local.instance_id}-pre-w2-t5-migration-app-data"
}

# ---------------------------------------------------------------------------
# Customer-managed KMS key (CMK) for the instance
#
# Encrypts the instance's storage volumes and Performance Insights data
# under a key Panakoes owns. Same rationale as the Aurora module's CMK:
# customer-controlled rotation cadence, scoped key policies, ability to
# disable the key in an incident to immediately freeze access to
# snapshots and replicas.
#
# This is a SEPARATE CMK from the Aurora module's CMK so the two modules
# can be applied / destroyed independently. After the burn-in window
# ends and the Aurora module is decommissioned (a separate PR), its CMK
# will be scheduled for deletion. The two never need to share key
# material because the migration is one-way: Aurora data is migrated
# into the RDS instance via pg_dump, not via snapshot restore.
# ---------------------------------------------------------------------------

resource "aws_kms_key" "auth_db_rds" {
  description             = "KMS key for the dev auth-db RDS PostgreSQL instance (storage + Performance Insights encryption)"
  enable_key_rotation     = true
  deletion_window_in_days = 7

  tags = local.common_tags
}

resource "aws_kms_alias" "auth_db_rds" {
  name          = "alias/${local.name_prefix}-auth-db-rds"
  target_key_id = aws_kms_key.auth_db_rds.key_id
}

# ---------------------------------------------------------------------------
# DB subnet group
#
# RDS requires a subnet group covering at least two AZs even for
# single-AZ instances (so failover can target a different AZ if
# `multi_az` is flipped on later). We span all three private subnets
# from `dev/network/` for the same reasons as the Aurora module.
# ---------------------------------------------------------------------------

resource "aws_db_subnet_group" "auth_db_rds" {
  name        = "${local.name_prefix}-auth-db-rds"
  description = "Subnet group spanning the three dev private subnets for the auth-db RDS instance"
  subnet_ids  = local.private_subnet_ids

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Instance security group
#
# Allow 5432/TCP inbound from the VPC CIDR (`10.10.0.0/16`) and nothing
# else. Same posture as the Aurora module's SG: wider than per-service
# allow-listing, but reachable from any ECS task / Lambda / Session
# Manager session inside this VPC without follow-up Terraform applies.
# ---------------------------------------------------------------------------

resource "aws_security_group" "auth_db_rds" {
  name        = "${local.name_prefix}-auth-db-rds-sg"
  description = "Allow 5432/TCP inbound to the dev auth-db RDS instance from the dev VPC CIDR"
  vpc_id      = local.vpc_id

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-auth-db-rds-sg"
  })
}

resource "aws_vpc_security_group_ingress_rule" "auth_db_rds_postgres_from_vpc" {
  security_group_id = aws_security_group.auth_db_rds.id
  description       = "Postgres 5432/TCP from inside the dev VPC"
  cidr_ipv4         = local.vpc_cidr_block
  ip_protocol       = "tcp"
  from_port         = 5432
  to_port           = 5432

  tags = local.common_tags
}

resource "aws_vpc_security_group_egress_rule" "auth_db_rds_egress_all" {
  security_group_id = aws_security_group.auth_db_rds.id
  description       = "Egress is unrestricted; RDS has no meaningful outbound surface, but the rule is explicit for least-surprise"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"

  tags = local.common_tags
}

# ---------------------------------------------------------------------------
# Master password resolution helpers
#
# Same pattern as the Aurora module. The README walks the operator
# through rotating to the Secrets-Manager-stored value via
# `aws rds modify-db-instance` after first apply.
# ---------------------------------------------------------------------------

resource "random_password" "master_password" {
  length      = 32
  special     = true
  min_lower   = 4
  min_upper   = 4
  min_numeric = 4
  min_special = 2

  override_special = "!#$%&*+,-.:;<=>?[]^_{|}~"
}

data "aws_secretsmanager_secret_version" "master_password" {
  count     = local.postgres_password_secret_arn != null ? 1 : 0
  secret_id = local.postgres_password_secret_arn
}

# ---------------------------------------------------------------------------
# Enhanced Monitoring IAM role
#
# Same pattern as the Aurora module. Lets `monitoring.rds.amazonaws.com`
# assume the role to stream OS-level metrics (CPU, memory, disk, network)
# to CloudWatch Logs. db.t4g.micro supports Enhanced Monitoring; we
# keep the 60-second granularity for parity with the Aurora module.
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
  name               = "${local.name_prefix}-auth-db-rds-enhanced-monitoring"
  description        = "RDS Enhanced Monitoring role for the dev auth-db RDS instance"
  assume_role_policy = data.aws_iam_policy_document.rds_monitoring_trust.json

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "rds_enhanced_monitoring" {
  role       = aws_iam_role.rds_enhanced_monitoring.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonRDSEnhancedMonitoringRole"
}

# ---------------------------------------------------------------------------
# RDS PostgreSQL instance (db.t4g.micro single-AZ)
#
# Why RDS Postgres over Aurora Serverless v2 for THIS workload:
#   - Auth-db storage is <100 MB at dev scale; Aurora's auto-scaling
#     storage is unused capacity we still pay for.
#   - Auth load is bursty but tiny (<5 QPS sustained); Aurora's ACU
#     auto-scaling is unused.
#   - Aurora Serverless v2 with `min_capacity_acu = 0` was costing 11.6s
#     cold-start on first sign-in after 5 min idle -- unacceptable UX.
#   - db.t4g.micro is always-on at $0/mo (Free Tier, 12 months) or
#     ~$12/mo after. No cold-start.
#   - Single-AZ is fine for the dev environment; production overrides
#     `multi_az` to true.
#
# Storage encryption uses the module-owned CMK so a single kms:Decrypt
# grant covers both the storage volume and Performance Insights data
# in any consumer IAM policy.
#
# Backup retention 7 days matches the Aurora module rationale; production
# bumps to 30. Deletion protection ON because losing the user table
# would log every user out of every dev environment.
#
# `apply_immediately = true` for dev: changes (engine version, instance
# class, parameter group) apply on the next maintenance window unless
# this flag is set, and dev iteration needs the changes now. Production
# should set this to false and rely on a scheduled maintenance window.
#
# Performance Insights at 7-day retention (free tier) gives parity with
# the Aurora module's tooling story so observability investment ports
# from dev to prod.
#
# Enhanced Monitoring at 60-second granularity gives per-minute OS
# metrics; lower intervals (1, 5, 10, 15, 30 seconds) increase the
# log-stream cost without buying us anything for a dev instance.
# ---------------------------------------------------------------------------

resource "aws_db_instance" "auth_db" {
  identifier = local.instance_id

  engine         = "postgres"
  engine_version = var.engine_version

  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage
  storage_type      = var.storage_type
  storage_encrypted = true
  kms_key_id        = aws_kms_key.auth_db_rds.arn

  db_name  = var.database_name
  username = var.master_username
  password = local.master_password
  port     = 5432

  db_subnet_group_name   = aws_db_subnet_group.auth_db_rds.name
  vpc_security_group_ids = [aws_security_group.auth_db_rds.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period = var.backup_retention_days
  backup_window           = "03:00-04:00" # UTC; pre-dawn US-east window

  deletion_protection      = true
  skip_final_snapshot      = true  # dev only; production sets false + final_snapshot_identifier
  delete_automated_backups = false # keep automated backups even after instance delete (until retention expires)

  apply_immediately = true

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = aws_kms_key.auth_db_rds.arn
  performance_insights_retention_period = 7

  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_enhanced_monitoring.arn

  auto_minor_version_upgrade = true

  tags = merge(local.common_tags, {
    Name = local.instance_id
  })

  lifecycle {
    # The master_password is rotated post-apply via the AWS CLI (see
    # README); ignoring the value here lets that rotation persist
    # without Terraform reverting it on the next apply.
    ignore_changes = [password]
  }
}

# ---------------------------------------------------------------------------
# W2-T5: snapshot + restore migration to the consolidated app-data CMK
# ---------------------------------------------------------------------------
#
# Why this exists:
#   `aws_db_instance.kms_key_id` is a ForceNew attribute. A naive in-place
#   flip from the module-local CMK to the consolidated `panakoes/app-data`
#   CMK would `1 destroy / 1 add` the live `auth_db` instance and lose the
#   on-volume Better-Auth user / session tables. The supported migration
#   pattern (per AWS RDS docs) is blue/green:
#
#     1) Take a manual snapshot of the v1 instance.
#     2) Copy the snapshot, re-encrypting under the target CMK.
#     3) Restore from the re-encrypted snapshot into a NEW v2 instance.
#     4) Switch consumers (the auth ECS task) from the v1 endpoint to the v2
#        endpoint by updating the Secrets Manager DSN.
#     5) After burn-in, retire v1 (W2-T7 retirement PR, OUT OF SCOPE here).
#
# This PR implements steps 1-3 as Terraform-managed resources and lays the
# groundwork for step 4 (the secrets module references `instance_endpoint_v2`
# in its `database-url` placeholder; the live cutover is still a manual
# `aws secretsmanager put-secret-value` per the README because the
# `database-url` secret carries `lifecycle { ignore_changes = [secret_string] }`
# by design).
#
# The original `aws_db_instance.auth_db` resource block ABOVE is intentionally
# unchanged. Any modification there would risk a ForceNew on `kms_key_id` or
# other immutable attributes and defeat the point of the migration. The
# orchestrator-only retirement (delete the v1 instance, the module-local
# `aws_kms_key.auth_db_rds`, and this snapshot pair) lands in a separate PR.
# ---------------------------------------------------------------------------

# Step 1: manual snapshot of the v1 instance.
#
# `aws_db_snapshot` is a one-shot Terraform resource: it triggers
# `CreateDBSnapshot` on apply and never re-snapshots on subsequent plans
# (the resource is keyed by `db_snapshot_identifier`, which we fix to a
# `-pre-w2-t5-migration` suffix). If the snapshot is deleted out of band
# the next plan recreates it; that is desirable for disaster recovery.
#
# Tags propagate to the snapshot at creation time. No additional encryption
# argument is needed; RDS encrypts the snapshot with the same CMK as the
# source instance (the module-local `aws_kms_key.auth_db_rds`).
resource "aws_db_snapshot" "pre_migration" {
  # NOTE: must use `.identifier` NOT `.id`. In the AWS provider, `aws_db_instance.id`
  # returns the AWS-internal `dbi_resource_id` (e.g., `db-B576OYW3V5...`), but
  # `CreateDBSnapshot` expects the user-facing DB instance identifier (e.g.,
  # `panakoes-dev-auth-rds`). Passing `.id` triggers `DBInstanceNotFound` at apply.
  # Discovered 2026-05-19 when PR #407 auto-apply failed on this exact resource.
  db_instance_identifier = aws_db_instance.auth_db.identifier
  db_snapshot_identifier = local.pre_migration_snapshot_id

  tags = merge(local.common_tags, {
    Name    = local.pre_migration_snapshot_id
    Purpose = "w2-t5-pre-migration-snapshot"
    Stage   = "1-of-3"
  })
}

# Step 2: copy the snapshot, re-encrypting under the consolidated CMK.
#
# `aws_db_snapshot_copy` invokes `CopyDBSnapshot` with a `kms_key_id`
# override, producing a new snapshot whose storage is encrypted under the
# target key. Cross-key re-encryption is a first-class AWS RDS operation
# (supported since 2017) and provider-side the resource has been GA in
# `hashicorp/aws` since v3.x; we are on v6.44.0. `copy_tags = true` copies
# the source snapshot's tag set so the new snapshot inherits the project /
# environment / module tags without restating them here.
#
# The copy is the second one-shot in the pair: same idempotency story as
# `aws_db_snapshot.pre_migration` above. Once both snapshots exist, the v2
# instance below restores from this copy (NOT from the v1 snapshot, which
# is still encrypted under the module-local CMK).
resource "aws_db_snapshot_copy" "re_encrypted" {
  source_db_snapshot_identifier = aws_db_snapshot.pre_migration.db_snapshot_arn
  target_db_snapshot_identifier = local.re_encrypted_snapshot_id

  kms_key_id = local.app_data_kms_key_arn
  copy_tags  = true

  tags = merge(local.common_tags, {
    Name    = local.re_encrypted_snapshot_id
    Purpose = "w2-t5-re-encrypted-snapshot"
    Stage   = "2-of-3"
  })
}

# Step 3: v2 instance, restored from the re-encrypted snapshot.
#
# Every operationally-relevant attribute mirrors `aws_db_instance.auth_db`
# (engine, version, instance class, storage, subnet group, security group,
# Performance Insights config, Enhanced Monitoring config). The four
# differences are:
#
#   1) `identifier` is `${local.name_prefix}-auth-rds-v2` so the two
#      instances coexist during the burn-in window.
#   2) `snapshot_identifier` points at the re-encrypted snapshot copy,
#      which gives the volume both the v1 data AND the new CMK.
#   3) `kms_key_id` is the consolidated `panakoes/app-data` CMK.
#   4) `performance_insights_kms_key_id` is also the consolidated CMK so
#      PI data and storage share the same key (same rationale as the v1
#      instance using the module-local key for both).
#
# `db_name`, `username`, and `password` are deliberately omitted: when
# restoring from a snapshot, RDS reuses the values baked into the snapshot
# (Postgres roles, default db name, master password) and Terraform must
# not pass them or apply fails with `InvalidParameterCombination`. The
# `ignore_changes` block extends that to subsequent plans so a future
# rotation against the v2 master password (via the same CLI flow as v1)
# is not reverted.
#
# `apply_immediately`, `deletion_protection`, `skip_final_snapshot`,
# `delete_automated_backups`, `backup_retention_period`, `backup_window`,
# `auto_minor_version_upgrade`, `publicly_accessible`, `multi_az`,
# `monitoring_interval`, and `monitoring_role_arn` are all carried over
# from v1 verbatim.
resource "aws_db_instance" "auth_db_v2" {
  identifier = local.instance_id_v2

  snapshot_identifier = aws_db_snapshot_copy.re_encrypted.id

  engine         = "postgres"
  engine_version = var.engine_version

  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage
  storage_type      = var.storage_type
  storage_encrypted = true
  kms_key_id        = local.app_data_kms_key_arn

  port = 5432

  db_subnet_group_name   = aws_db_subnet_group.auth_db_rds.name
  vpc_security_group_ids = [aws_security_group.auth_db_rds.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period = var.backup_retention_days
  backup_window           = "03:00-04:00"

  deletion_protection      = true
  skip_final_snapshot      = true
  delete_automated_backups = false

  apply_immediately = true

  performance_insights_enabled          = true
  performance_insights_kms_key_id       = local.app_data_kms_key_arn
  performance_insights_retention_period = 7

  monitoring_interval = 60
  monitoring_role_arn = aws_iam_role.rds_enhanced_monitoring.arn

  auto_minor_version_upgrade = true

  tags = merge(local.common_tags, {
    Name           = local.instance_id_v2
    MigrationStage = "v2-snapshot-restored"
  })

  lifecycle {
    # `password`, `db_name`, `username` are inherited from the snapshot;
    # rotating the master password via the AWS CLI must not be reverted
    # by Terraform on subsequent applies (same pattern as the v1
    # instance). `snapshot_identifier` is ignored so that future
    # re-snapshots / re-restores do not appear as drift on every plan;
    # if a fresh restore is needed, taint the resource explicitly.
    ignore_changes = [
      password,
      snapshot_identifier,
    ]
  }
}

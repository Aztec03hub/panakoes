# Dev Environment Backup

Per-environment Terraform configuration creating the AWS Backup vault
and plan that protect the Panakoes `dev` environment's stateful
resources. Consumes the S3 remote state backend created by
`infra/bootstrap/`; state lives at `dev/backup/terraform.tfstate`.

## What this creates

- A dedicated KMS CMK aliased `alias/panakoes-dev-backup` with
  rotation enabled and a 7-day deletion window. Encrypts every
  recovery point in the vault.
- An `aws_backup_vault` named `panakoes-dev`, KMS-encrypted with the
  CMK above.
- An `aws_backup_plan` named `panakoes-dev-daily-monthly` with two
  rules:
  - **daily-30d-retention**: fires at 05:00 UTC every day
    (`cron(0 5 ? * * *)`); retains recovery points for 30 days.
    5-hour start window, 7-hour completion window.
  - **monthly-365d-retention**: fires at 05:00 UTC on the 1st of each
    month (`cron(0 5 1 * ? *)`); retains recovery points for 365
    days.
- An `aws_backup_selection` attached to the plan that protects the
  three DynamoDB tables created by `infra/dev/data/` (ingestion,
  audit-log, streaming-sessions) and the Aurora Serverless v2 auth-db
  cluster created by `infra/dev/auth-db/`. Resources are listed by ARN
  AND by the `Backup = enabled` tag so the selection works the moment
  this module is applied and continues to grow as new tagged resources
  are added. The Aurora cluster ARN was added after the PR #282
  restore drill confirmed native Aurora PITR (7-day window) was
  working but the AWS Backup vault held zero Aurora recovery points;
  daily + monthly snapshots into the vault now ride on top of native
  PITR and unlock the future cross-region copy path.
- An IAM service role `panakoes-dev-backup` that AWS Backup assumes,
  with the AWS-managed `AWSBackupServiceRolePolicyForBackup` and
  `AWSBackupServiceRolePolicyForRestores` policies attached.
- An `aws_backup_vault_notifications` resource publishing key vault
  events (BACKUP_JOB_FAILED, BACKUP_JOB_EXPIRED, RESTORE_JOB_FAILED,
  COPY_JOB_FAILED, RECOVERY_POINT_MODIFIED) to the project's
  system-alerts SNS topic.

## Why two rules

A single plan with two rules captures two distinct recovery
horizons in one apply:

| Rule    | Cadence | Retention | Use case                              |
|---------|---------|-----------|---------------------------------------|
| daily   | 24h     | 30 days   | "I just corrupted the table"          |
| monthly | 1st     | 365 days  | "What did the data look like in Q3?"  |

Daily backups give us a ~24-hour RPO and a fast restore path for the
most common recovery scenario. Monthly backups give a long horizon
for forensic and compliance queries without the storage cost of
keeping a year of daily snapshots.

The plan deliberately does NOT replicate to a second region or
account; the dev tier accepts blast-radius exposure to keep costs
low. Production should add cross-region and cross-account copies.

## Why a 7-day KMS deletion window

This module mirrors `infra/dev/secrets/`'s 7-day window rather than
the 30-day window used by S3 / DynamoDB CMKs in sibling modules. The
backup vault is a secondary recovery target; primary recovery in dev
sits in DynamoDB PITR (which is enabled on every table per the data
module). A fat-finger destroy of this module's CMK would force a
regeneration of the vault and the loss of accumulated recovery
points, but live data continues to be recoverable via PITR. The
7-day window is short enough not to strand the team for a month
while still leaving a recovery path for accidental deletes.

## Why list resources by ARN AND by tag

`aws_backup_selection` accepts both an explicit `resources` list and
a tag-based `selection_tag` block; AWS Backup unions the two. Listing
the current dev tables by ARN means the protection takes effect on
this module's first apply, even before the `Backup = enabled` tag
ships in `infra/dev/data/` (tracked in a follow-up). The tag block
becomes the steady-state mechanism: new tables tagged
`Backup = enabled` are picked up automatically without editing this
file.

## Cost expectations

- **KMS**: $1/month for the dedicated CMK plus per-request charges
  amortized by AWS Backup's snapshot-level encryption.
- **Backup storage**: warm storage is $0.05/GB-month. Dev tables are
  KB-to-MB scale; expect single-digit cents per month from storage.
- **Backup jobs**: free for AWS-native services (DynamoDB, EBS, RDS,
  EFS); jobs themselves cost nothing, only the storage they produce.
- **Restore tests**: $0.02/GB for the first 1 GB of restored data
  per month, free thereafter. Run quarterly restore drills to
  validate the recovery procedure without significant cost.

Total expected monthly run rate at dev volumes: well under $5.

## Apply

    cd infra/dev/backup
    AWS_PROFILE=lafayettelabs terraform init
    AWS_PROFILE=lafayettelabs terraform plan
    AWS_PROFILE=lafayettelabs terraform apply

`terraform init` downloads the AWS provider and initializes the S3
backend (talks to the bucket created by `infra/bootstrap/`).

## Post-apply: tag the protected tables

After this module applies, add `Backup = enabled` to the three
DynamoDB tables in `infra/dev/data/main.tf` so the tag-selection path
becomes authoritative going forward. This is a follow-up PR; until
it lands, the explicit ARN list keeps the tables protected.

## Restore drill (manual, quarterly)

The whole point of backups is restoration. Run a drill quarterly.

For the auth Aurora cluster, use
[`docs/runbooks/aurora-restore-drill.md`](../../../docs/runbooks/aurora-restore-drill.md);
that runbook exercises Aurora native PITR end to end (the auth cluster
is NOT yet in this module's `aws_backup_selection`, so the restore
path is RDS-native, not AWS Backup). First successful run: 2026-05-11.

For the DynamoDB tables currently in the selection
(`panakoes-dev-ingestion`, `panakoes-dev-audit-log`,
`panakoes-dev-streaming-sessions`), the AWS Backup restore procedure
is:

1. Pick a recovery point: `aws backup list-recovery-points-by-backup-vault --backup-vault-name panakoes-dev`.
2. Restore to a temporary table name:
   `aws backup start-restore-job --recovery-point-arn <arn> --metadata <metadata-json> --iam-role-arn <role-arn>`.
3. Verify item count and a known sample row matches expectations.
4. Delete the restored table.
5. Record the restore time and any issues in the operations log.

Quarterly drills catch policy drift (e.g., the IAM role losing
permissions, KMS key access being revoked) before a real incident.

## Consuming outputs from other configs

Downstream Terraform configurations read backup metadata via a
`terraform_remote_state` data source pointing at this state:

    data "terraform_remote_state" "backup" {
      backend = "s3"
      config = {
        bucket = "panakoes-tf-state-b291597a"
        key    = "dev/backup/terraform.tfstate"
        region = "us-east-1"
      }
    }

    # Then reference outputs as:
    #   data.terraform_remote_state.backup.outputs.vault_arn
    #   data.terraform_remote_state.backup.outputs.plan_id
    #   data.terraform_remote_state.backup.outputs.service_role_arn

Reuse `service_role_arn` in any new `aws_backup_selection` defined in
another module so the same IAM identity executes every project
backup. Reference `plan_id` to attach additional resources to the
same plan rather than creating a parallel plan with the same
schedule.

## Outputs

| Output                      | Type   | Purpose                                                 |
|-----------------------------|--------|---------------------------------------------------------|
| `vault_arn`                 | string | ARN of the legacy dev backup vault                      |
| `vault_name`                | string | Name of the legacy dev backup vault                     |
| `plan_arn`                  | string | ARN of the legacy daily/monthly backup plan             |
| `plan_id`                   | string | ID of the legacy backup plan (for downstream selections) |
| `kms_key_arn`               | string | ARN of the legacy per-vault CMK                         |
| `service_role_arn`          | string | ARN of the AWS Backup service role (shared by both vaults) |
| `consolidated_vault_arn`    | string | ARN of the W2-T3 parallel vault under the shared CMK    |
| `consolidated_vault_name`   | string | Name of the W2-T3 parallel vault                        |
| `consolidated_plan_arn`     | string | ARN of the W2-T3 parallel daily/monthly backup plan     |
| `consolidated_plan_id`      | string | ID of the W2-T3 parallel backup plan                    |

## W2-T3 parallel vault under the consolidated CMK

The Wave 2 KMS consolidation (`infra/dev/kms/`, W2-T1) introduced a
shared `alias/panakoes/app-data` CMK that replaces the per-service
CMKs across the dev stack. The backup vault was deferred out of the
initial W2-T2..T6 bundle because `aws_backup_vault.kms_key_arn` is a
ForceNew attribute: flipping it in place on `aws_backup_vault.dev`
would destroy the live vault and lose every accumulated recovery
point. DynamoDB native PITR is the primary recovery mechanism so
live data was never at risk, but the secondary recovery-point
history (27 points at the time of W2-T2..T6) is real and worth
preserving.

This module now provisions a SECOND vault, plan, selection, and
notification resource in parallel:

| Resource | Legacy (original) | Consolidated (new) |
|---|---|---|
| Vault | `aws_backup_vault.dev` (`panakoes-dev`) | `aws_backup_vault.consolidated` (`panakoes-dev-consolidated`) |
| Plan | `aws_backup_plan.dev` (`panakoes-dev-daily-monthly`) | `aws_backup_plan.consolidated` (`panakoes-dev-consolidated-daily-monthly`) |
| Selection | `aws_backup_selection.dev` (`panakoes-dev-dynamodb`) | `aws_backup_selection.consolidated` (`panakoes-dev-consolidated-dynamodb`) |
| Notifications | `aws_backup_vault_notifications.dev` | `aws_backup_vault_notifications.consolidated` |
| CMK | `aws_kms_key.backup` (per-vault CMK) | `data.terraform_remote_state.kms.outputs.app_data_key_arn` (shared) |
| IAM role | `aws_iam_role.backup` | `aws_iam_role.backup` (reused) |

The two vaults take identical daily (30-day retention) + monthly
(365-day retention) snapshots of the same DynamoDB tables. The only
differences are vault name, plan name, selection name, and the CMK
that encrypts each recovery point.

### Cutover sequence (multi-PR)

This PR is **step 1 of 3**. Steps 2 and 3 are separate follow-up
PRs filed after enough wall-clock time has elapsed for the new
vault to accumulate parity coverage.

1. **Apply this PR.** The parallel vault, plan, selection, and
   notifications begin operating immediately. From this moment on,
   every daily and monthly job writes recovery points to BOTH vaults.
   AWS Backup bills storage per vault, so this roughly doubles the
   backup storage cost during the parallel window. At dev volumes
   that doubling is still cents per month.

2. **Day +30: optional daily-rule cutover.** Once the parallel
   vault has accumulated a full 30-day rolling window of daily
   recovery points (the matching coverage the legacy vault has
   today), the legacy daily rule may be removed in a follow-up PR.
   Keeping both daily rules running through Day 365 is cheap and
   conservative; the early-cutover option exists only for teams
   that want to cap parallel storage cost.

3. **Day +365: full retirement of the legacy stack.** Once the
   parallel vault has accumulated a full year of monthly recovery
   points (matching the legacy vault's monthly coverage), the
   legacy vault can be retired in a follow-up PR:
   - Manually delete every remaining recovery point in
     `aws_backup_vault.dev` via
     `aws backup delete-recovery-point --backup-vault-name panakoes-dev --recovery-point-arn <arn>`
     (the legacy vault has `force_destroy = false` and Terraform
     will refuse to destroy a non-empty vault).
   - Remove `aws_backup_vault.dev`, `aws_backup_plan.dev`,
     `aws_backup_selection.dev`,
     `aws_backup_vault_notifications.dev`, `aws_kms_key.backup`,
     and `aws_kms_alias.backup` from `main.tf`.
   - Remove the `kms_key_arn` output from `outputs.tf` (downstream
     consumers should already be pointed at the consolidated CMK
     via `infra/dev/kms/` by this point).
   - Schedule the legacy CMK deletion. The CMK has a 7-day
     deletion window; after the destroy applies, AWS schedules the
     key for deletion 7 days later and can be cancelled within
     that window.

### Why reuse the same IAM role

`aws_iam_role.backup` already trusts `backup.amazonaws.com` and has
the AWS-managed `AWSBackupServiceRolePolicyForBackup` and
`AWSBackupServiceRolePolicyForRestores` policies attached. Those
policies grant the actions AWS Backup needs against the DynamoDB
source tables and the recovery-point target vault; neither vault
identity is encoded in the policy, so a single role can back many
vault selections. Reusing the role avoids a permission drift surface
(two roles with subtly different attached policies) and keeps the
IAM blast radius identical to the pre-migration state.

### KMS access requirement

For the AWS Backup service role to encrypt recovery points under the
consolidated CMK, the CMK's key policy must grant
`backup.amazonaws.com` (or this module's `aws_iam_role.backup`) the
standard set of encrypt/decrypt actions. The consolidated CMK in
`infra/dev/kms/` was provisioned with a broad in-account key policy
covering the AWS Backup service principal as one of its consumers;
no additional key-policy change is required as part of this PR. If a
future tightening of the consolidated CMK's policy removes Backup
access, the parallel vault's jobs will fail with a KMS
AccessDenied error, which the notifications resource will surface
via the existing system-alerts SNS topic.

## Open follow-ups

- Add `Backup = enabled` tag to every protected table in
  `infra/dev/data/main.tf`.
- Stand up `infra/dev/events/` with the system-alerts SNS topic and
  an SNS topic policy granting `backup.amazonaws.com` `sns:Publish`.
  Replace the constructed ARN in `data.tf` with a
  `terraform_remote_state` lookup once the module exists.
- Consider cross-region copy (us-west-2) for production; out of
  scope for dev.
- Wire CloudWatch alarms on the SNS topic to PagerDuty / email once
  the on-call rotation exists.
- Step 2 (optional, Day +30) and step 3 (required, Day +365 or later)
  of the W2-T3 cutover sequence above.

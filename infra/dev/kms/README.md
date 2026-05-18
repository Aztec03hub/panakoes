# `infra/dev/kms/`

Two consolidated Customer Managed Keys (CMKs) for the dev environment, introduced as Wave 2 Task 1 of the infrastructure migration (see `ARCH-MIGRATION.md` section 2.2).

## Purpose

Reduce the dev CMK footprint from 19 service-specific keys ($19/month) to 4 operational keys ($4/month) by consolidating onto two general-purpose keys created here, plus the two pre-existing keys that stay separate by design.

## Resources created (this PR)

- `aws_kms_key.app_data` + `aws_kms_alias.app_data` (`alias/panakoes/app-data`)
- `aws_kms_key.logs` + `aws_kms_alias.logs` (`alias/panakoes/logs`)

Planned: 4 to add, 0 to change, 0 to destroy.

## Cost impact

| Stage | Change | Net gross cost |
|---|---|---|
| This PR (W2-T1) | +2 keys at $1/mo each | +$2/mo |
| W2-T2..T7 (consumer migrations + old-key deletion) | retire 15 of the 19 old service-specific CMKs | -$13/mo vs. pre-Wave-2 baseline |

The +$2/mo cost is paid immediately upon apply; the -$15/mo savings lands when the old keys reach the end of their `PendingDeletion` window after W2-T7 deletes them.

## Forward references (consumers, not wired yet)

The new keys are not yet referenced by any service. Wave 2 follow-up PRs:

| Task | Modules touched | Migrates onto |
|---|---|---|
| W2-T2 | `infra/dev/storage/`, `infra/dev/frontend/` | `alias/panakoes/app-data` (S3 buckets) |
| W2-T3 | `infra/dev/secrets/`, `infra/dev/events/`, `infra/dev/ecr/` | `alias/panakoes/app-data` (Secrets Manager, SQS/SNS, ECR) |
| W2-T4 | `infra/dev/observability/`, `infra/dev/api-gateway/`, scattered log-group refs | `alias/panakoes/logs` (CloudWatch Logs groups) |
| W2-T5 | `infra/dev/auth-db-rds/` | `alias/panakoes/app-data` (RDS storage; requires reboot window) |
| W2-T6 | `infra/dev/ecs/*.tf` | task-definition `kmsKeyId` references |
| W2-T7 | AWS CLI, no Terraform | schedule the 15 old CMKs for deletion (7-day window) |

## Key separation rationale (what is NOT in this module)

Two CMKs are intentionally kept separate from the consolidated keys:

- **JWT signing key** (`alias/panakoes-dev-jwt-signing`, managed by `infra/dev/auth-kms-signing/`). Asymmetric RSA_2048 for RS256 JWS. Kept separate so that a compromise of the symmetric app-data key cannot enable forged auth tokens.
- **Terraform state key** (`alias/panakoes-tf-state`, managed by `infra/bootstrap/`). Kept separate so that the bootstrap key for the rest of the keystore is not itself encrypted under a key managed by the same Terraform state it protects.

## Backend

State key: `dev/kms/terraform.tfstate` in the shared S3 backend bucket. Apply with `panakoes-admin` profile, region `us-east-1`.

## Related

- `infra/dev/auth-kms-signing/` -- JWT signing CMK (untouched by Wave 2).
- `infra/bootstrap/` -- Terraform-state CMK (untouched by Wave 2).
- `ARCH-MIGRATION.md` section 2.2 -- full Wave 2 mapping table and re-encryption plan.

# Disaster Recovery Runbook

## When to use this runbook

Reach for this runbook when a foundational system has been corrupted, deleted, or rendered inaccessible and normal operations cannot resume without an explicit recovery procedure. Concretely:

- Terraform remote state in S3 is corrupted, partially deleted, or out of sync with reality.
- An RDS Postgres instance is lost, unrecoverable, or has data corruption requiring a point-in-time restore.
- A DynamoDB table is corrupted, accidentally deleted, or has a logical-corruption window that needs to be unwound.
- An S3 bucket has had objects deleted or overwritten and prior versions must be restored.
- An ECR image was overwritten with a broken build and a prior digest must be re-promoted.
- The GitHub repository itself becomes unavailable (account compromise, repo deletion, permission issue).

Routine bug fixes, incident triage, and rollbacks of normal deployments belong in `incident-response.md`. This file is for foundational data and infrastructure recovery.

## Prerequisites

- AWS CLI authenticated against the Panakoes AWS account (`aws sts get-caller-identity` returns the expected account).
- Access to the GitHub repo (admin, since some recovery paths involve secrets or branch protection bypass).
- Terraform CLI installed (`terraform -version`); pinned version per `infra/` requirements.
- `gh` CLI authenticated (`gh auth status`).
- Read access to the run-history records in `.agent-runs/` (local) plus PRs in GitHub for forensics.

## Recovery decision flowchart

Pick the lane based on what is broken. Lanes are independent; combine them only when multiple systems are simultaneously affected.

```
What is broken?
|
+-- Terraform state -----------> Lane A: Terraform State Recovery
|
+-- RDS Postgres data ---------> Lane B: RDS Postgres Restore (PITR)
|
+-- DynamoDB table ------------> Lane C: DynamoDB PITR Restore
|
+-- S3 object(s) deleted ------> Lane D: S3 Versioning Rollback
|
+-- ECR image overwritten -----> Lane E: ECR Image Retag / Rollback
|
+-- GitHub repo unavailable ---> Lane F: GitHub Repo Recovery
```

If multiple lanes apply, run them in this order: F, A, B, C, D, E. Repo first (so source-of-truth code is reachable), then state (so infra is describable), then data stores (so applications have something to read), then storage and images.

## Lane A: Terraform State Recovery

State lives in S3 (KMS-encrypted) with a DynamoDB lock table. See `infra/bootstrap/` for the canonical bootstrap module and ADR-004 in `PLANNING.md` for the locked decision.

### Procedure

1. **Confirm the failure mode.** Run `terraform plan` from the affected `infra/<env>/<module>/` directory. Capture the error verbatim. State corruption symptoms include "state lock could not be acquired" that persists past 2 minutes, "no valid credential" against the backend bucket, or `terraform plan` reporting wholesale resource recreation against known-existing AWS resources.
2. **Snapshot what survives.** Before changing anything, list and copy every state object that still exists in S3:
   ```bash
   aws s3 ls s3://<state-bucket>/ --recursive
   aws s3 cp s3://<state-bucket>/ ./recovery-snapshot/ --recursive
   ```
   Store the snapshot outside the repo. This is your forensic record and your last-resort fallback.
3. **Inspect existing state versions.** S3 versioning is enabled on the state bucket. List object versions for the broken key:
   ```bash
   aws s3api list-object-versions \
     --bucket <state-bucket> \
     --prefix <env>/<module>/terraform.tfstate
   ```
   Identify a `VersionId` from before the corruption window.
4. **Restore the prior version (lowest-impact path).**
   ```bash
   aws s3api copy-object \
     --bucket <state-bucket> \
     --copy-source <state-bucket>/<env>/<module>/terraform.tfstate?versionId=<good-version-id> \
     --key <env>/<module>/terraform.tfstate
   ```
   Re-run `terraform plan` and confirm it shows zero changes against the live AWS resources.
5. **Break a stuck lock if needed.** If the lock table holds a stale lock (the original locking process is gone), force-unlock with the lock ID surfaced by `terraform plan`:
   ```bash
   terraform force-unlock <lock-id>
   ```
   Do this only after confirming no other Terraform run is in progress. Coordinate via the system-alerts channel.
6. **Last resort: rebuild state from scratch via `infra/bootstrap/`.** If the backend bucket itself is destroyed or the state cannot be recovered, re-run the bootstrap module to recreate the backend, then `terraform import` each managed resource one at a time. This is slow and error-prone; use it only when versioning rollback fails. Steps:
   ```bash
   cd infra/bootstrap/
   terraform init
   terraform apply
   # then for each affected module
   cd ../<env>/<module>/
   terraform init
   terraform import <resource_address> <aws_resource_id>
   terraform plan  # iterate until zero changes
   ```
7. **Verification.** From every affected module, `terraform plan` reports no drift. Re-encrypt or rotate the KMS key only if the original CMK is compromised (see `SECURITY.md`).

### Rollback

State recovery is itself a rollback. If the recovery makes things worse, restore the snapshot from step 2:
```bash
aws s3 cp ./recovery-snapshot/<env>/<module>/terraform.tfstate \
  s3://<state-bucket>/<env>/<module>/terraform.tfstate
```

## Lane B: RDS Postgres Restore (Point-in-Time)

> Note: RDS is not yet provisioned in dev as of this runbook's authoring. The procedure below is the documented intent; specifics (instance identifier, parameter group, subnet group) become concrete when `infra/dev/data/` adds the RDS module. Update this section in the same PR that lands the RDS Terraform.

### Procedure

1. **Identify the recovery window.** Determine the latest restorable time before the corruption (e.g., immediately before the bad migration, the bad data delete, the bad app deploy). Capture as ISO 8601 UTC.
2. **Confirm automated backups are present.**
   ```bash
   aws rds describe-db-instances \
     --db-instance-identifier <db-id> \
     --query 'DBInstances[0].LatestRestorableTime'
   ```
3. **Restore to a new instance.** PITR always restores to a new instance; never overwrite the live one until cutover.
   ```bash
   aws rds restore-db-instance-to-point-in-time \
     --source-db-instance-identifier <db-id> \
     --target-db-instance-identifier <db-id>-restored-$(date +%Y%m%d-%H%M) \
     --restore-time <ISO-8601-UTC>
   ```
4. **Validate the restored instance.** Connect, run smoke queries against critical tables (users, transcripts, billing), confirm row counts and a known-good record exist.
5. **Cut traffic over.** Update the app's database URL secret in AWS Secrets Manager (key `panakoes/dev/postgres/url` or the matching environment key). Roll the dependent ECS services or Lambdas so they pick up the new DSN:
   ```bash
   aws secretsmanager update-secret \
     --secret-id panakoes/dev/postgres/url \
     --secret-string '<new-postgres-uri>'
   aws ecs update-service --cluster <cluster> --service <service> --force-new-deployment
   ```
6. **Decommission the old instance** only after confirming the cutover worked end to end (24 hours minimum hold).

### Rollback

If the restored instance has issues, swap the secret back to the original instance's URI and force a redeployment. The old instance was never modified, so the rollback is a single-secret-update plus deployment.

## Lane C: DynamoDB PITR Restore

DynamoDB tables in `infra/dev/data/` have point-in-time recovery enabled. Restore is per-table, restores into a new table name.

### Procedure

1. **Identify the affected table and corruption window.**
2. **Confirm PITR is enabled and capture the earliest/latest restorable times.**
   ```bash
   aws dynamodb describe-continuous-backups --table-name <table>
   ```
3. **Restore to a new table.**
   ```bash
   aws dynamodb restore-table-to-point-in-time \
     --source-table-name <table> \
     --target-table-name <table>-restored-$(date +%Y%m%d-%H%M) \
     --restore-date-time <ISO-8601-UTC> \
     --use-latest-restorable-time   # OR --restore-date-time
   ```
   The restore copies items, indexes, encryption settings, and tags. It does NOT copy stream settings, IAM, autoscaling, or TTL; reapply those.
4. **Validate.** Spot-check key items via `aws dynamodb get-item` against expected values.
5. **Cut traffic over.** Two paths, depending on application coupling:
   - **Atomic table rename (preferred):** delete the corrupted table and rename the restored table to the original name. This requires the writers to tolerate a brief outage and any downstream IAM policies referencing the table ARN may need refreshing if ARNs change.
   - **Config swap:** update the application config (env var or SSM parameter) pointing services at the new table name; redeploy.
6. **Decommission the corrupted table** after a 24-hour hold.

### Rollback

The original (corrupted) table remains intact during the restore. Revert by switching the application config back; no data is lost on the original.

## Lane D: S3 Versioning Rollback

All Panakoes S3 buckets in `infra/dev/storage/` (audio uploads, transcripts, log archive) have versioning enabled. Recovery is per-object; there is no built-in "restore the bucket as of timestamp T."

### Procedure

1. **Identify the affected key(s).** From CloudTrail or the application audit log, determine which keys were deleted or overwritten and roughly when.
2. **List the object's versions.**
   ```bash
   aws s3api list-object-versions --bucket <bucket> --prefix <key>
   ```
   Each entry has `VersionId`, `LastModified`, and `IsLatest`. A `DeleteMarker` indicates a soft-delete; the prior version is the data to restore.
3. **Restore by removing the delete marker** (if the key was deleted):
   ```bash
   aws s3api delete-object \
     --bucket <bucket> \
     --key <key> \
     --version-id <delete-marker-version-id>
   ```
4. **Restore by copying a prior version** (if the key was overwritten):
   ```bash
   aws s3api copy-object \
     --bucket <bucket> \
     --copy-source "<bucket>/<key>?versionId=<good-version-id>" \
     --key <key>
   ```
5. **Bulk restore** for many keys: use `aws s3api list-object-versions` with `--prefix`, pipe through `jq` to filter to the corruption window, and loop the copy-or-delete-marker operation per entry. Track progress; this can be slow for large prefixes.
6. **Verification.** Re-fetch the object and confirm size, MD5, and content match the pre-incident expectation.

### Rollback

Versioning preserves the bad version too. Reverse the recovery by copying the bad version back into the live key, or by adding a delete marker if the recovery was over-aggressive.

## Lane E: ECR Image Retag / Rollback

ECR repositories store Docker images for every Panakoes microservice. Tag immutability is configurable; if a tag was force-pushed (or moved), the prior image's digest still exists until lifecycle policy expires it.

### Procedure

1. **Identify the affected repository and the digest of the known-good image.**
   ```bash
   aws ecr describe-images \
     --repository-name <repo> \
     --query 'imageDetails[*].[imageDigest,imageTags,imagePushedAt]' \
     --output table
   ```
2. **Retag the known-good digest** so deployments pick it up:
   ```bash
   GOOD_DIGEST="sha256:<digest>"
   MANIFEST=$(aws ecr batch-get-image \
     --repository-name <repo> \
     --image-ids imageDigest=$GOOD_DIGEST \
     --query 'images[0].imageManifest' \
     --output text)
   aws ecr put-image \
     --repository-name <repo> \
     --image-tag <tag-to-restore> \
     --image-manifest "$MANIFEST"
   ```
3. **Roll the consuming service.** ECS Fargate services with the affected image must be redeployed to pull the new tag-to-digest mapping:
   ```bash
   aws ecs update-service --cluster <cluster> --service <service> --force-new-deployment
   ```
   For Lambda container images, update the function's `ImageUri` to the digest form, which avoids tag-mutability ambiguity:
   ```bash
   aws lambda update-function-code \
     --function-name <fn> \
     --image-uri <account>.dkr.ecr.<region>.amazonaws.com/<repo>@$GOOD_DIGEST
   ```
4. **Verification.** Hit the service health endpoint, confirm CloudWatch logs show the expected image hash on startup.

### Rollback

If the retag breaks the deployment, retag again with the prior tag-to-digest mapping and force a new deployment. Both digests still live in ECR until lifecycle expiry.

## Lane F: GitHub Repo Recovery

GitHub is the canonical source of truth for code. The repo is **public**, hosts **all history on GitHub**, and **does not use Git LFS**. There is no separate full-history backup; the recovery posture relies on:

- Every developer machine carries a full clone (full history). Any local clone can be pushed to a fresh remote.
- The repo is mirrorable to a future LaFayette Labs GitHub organization (per `CLAUDE.md` "Project Snapshot"); mirroring sooner is on the roadmap.
- Configuration that lives outside git (branch protection rules, GitHub Actions secrets, repo settings) must be re-applied via terraform-github or via `gh` CLI scripts; these are NOT in any local clone.

### Procedure

1. **If the repo is unavailable due to GitHub outage:** wait. Check `https://www.githubstatus.com/`. No action needed locally; clones already in flight remain functional offline.
2. **If the repo is deleted or the account is compromised:**
   1. Restore account access first (GitHub account recovery, 2FA backup codes).
   2. From any local clone with intact history (Phil's primary dev machine, any agent worktree, any active CI runner), create a fresh repo and push:
      ```bash
      cd /mnt/c/Users/plafayette/Documents/Facebook/panakoes
      git remote rename origin origin-broken  # or remove
      gh repo create <new-org-or-user>/panakoes --public --source=. --push
      ```
   3. Re-apply branch protection: required PR reviews 0 (per `workflow_panakoes_pr_flow` memory), required status checks (changelog-check, gitleaks, codeql, pytest, vitest, terraform-ci), linear history, no force-push.
   4. Re-add GitHub Actions secrets (AWS OIDC role ARN is in Terraform outputs; rerun `infra/global/` to surface).
   5. Re-add Dependabot configuration if any.
3. **Verify integrity.** `git fsck --full` on the local clone before pushing. Compare commit counts and the most-recent commit hash with what an external mirror or another clone shows.

### Rollback

If the new repo is wrong (forked from a stale clone, missing branches), do not delete it; rename it and push the corrected version into a second new repo. Then point local clones at the corrected remote.

## Verification (post-recovery, all lanes)

For each lane invoked, confirm before declaring recovery complete:

1. The directly affected system answers a smoke test (Terraform plan clean, RDS query returns expected rows, DynamoDB get-item returns expected value, S3 head-object returns expected size, ECR image hash matches deployed task, `git log` shows expected history).
2. Downstream systems are still healthy. Run end-to-end smoke (`make e2e` or the equivalent Playwright suite once available) against the dev environment.
3. CloudWatch alarms in the `panakoes-dev-system-alerts` SNS topic are not firing.
4. The audit log table `panakoes-dev-audit-log` shows the recovery action recorded by whoever performed it (manual record if the recovery action did not flow through the audit library).

## References

- `CLAUDE.md` "Locked Architectural Decisions" table for AWS region (us-east-1) and IaC tool (Terraform with remote S3 + KMS + DynamoDB lock).
- `PLANNING.md` ADR-004 for the Terraform remote-state decision and ADR-020 for the public-repo security posture.
- `infra/bootstrap/` for the canonical state-backend bootstrap module.
- `infra/dev/data/` for DynamoDB table definitions (PAY_PER_REQUEST, SSE, PITR, deletion protection per `infra/README.md` "Conventions").
- `infra/dev/storage/` for S3 bucket definitions (versioning, KMS-encrypted, public-access blocked, TLS-only).
- `incident-response.md` for the broader incident workflow that decides when to enter this runbook.
- `SECURITY.md` for KMS rotation and secret-rotation procedures.

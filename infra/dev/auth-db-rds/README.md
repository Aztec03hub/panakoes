# `infra/dev/auth-db-rds/`

RDS PostgreSQL 16 instance (`db.t4g.micro`, single-AZ) backing Better-Auth's tables for the auth microservice in the `dev` environment.

## Why this module exists

Replaces `infra/dev/auth-db/` (Aurora Serverless v2) for the auth-db specifically. The Aurora module remains in the repo for the duration of the burn-in window (7 days post-cutover), then is decommissioned in a follow-up PR.

**Root cause for the migration:** Aurora Serverless v2 with `min_capacity_acu = 0` (the dev cost-saver setting) cold-starts on first sign-in after 5 min idle. Measurement: cold sign-in = 11,963 ms total, of which ~11,500 ms is Aurora's resume-from-pause sequence (confirmed by client curl timing, API Gateway `integrationLatency`, and Aurora `ServerlessDatabaseCapacity` metric all agreeing). See `.agent-runs/2026-05-12T23-25-00Z-auth-coldstart-research.md` for the full investigation.

**Why RDS not Aurora keep-warm:** the workload (tiny storage, <5 QPS sustained, bursty at sign-in) doesn't use any Aurora-specific capability (auto-scaling storage, multi-AZ replication, read replicas). Aurora at `min_capacity_acu = 0.5` (no cold start) would cost ~$43/mo for capabilities the auth-db doesn't need. RDS db.t4g.micro is always-on, $0/mo for 12 months on AWS Free Tier (then ~$12/mo), no cold-start.

## Cutover sequence (one-time, manual)

There are TWO migration events for this module:

1. **Aurora to v1 RDS cutover (original migration, 2026-05-12):** the initial
   pg_dump from the retired Aurora cluster into the v1 RDS instance. Steps in
   the "Aurora to v1 RDS" sub-section below.
2. **v1 RDS to v2 RDS (W2-T5 KMS re-encryption, 2026-05-19):** the snapshot
   restore that moves the auth-db storage onto the consolidated
   `panakoes/app-data` CMK. Steps in the "v1 to v2 KMS migration" sub-section
   below.

### Aurora to v1 RDS (original, retained for historical reference)

After `terraform apply` provisions the v1 instance:

```bash
# 1. Get both DSNs (the live Aurora DSN was stored under panakoes-dev/database-url
#    BEFORE the W2-T5 migration; the secret name `panakoes-dev/postgres-auth-db-dsn`
#    referenced in earlier revisions of this README never existed -- it was a
#    typo for `panakoes-dev/database-url`).
OLD_DSN=$(aws secretsmanager get-secret-value --secret-id panakoes-dev/database-url --query SecretString --output text)
NEW_PW=$(aws secretsmanager get-secret-value --secret-id panakoes-dev/postgres-auth-db-password --query SecretString --output text)
NEW_HOST=$(cd infra/dev/auth-db-rds && terraform output -raw instance_address)
NEW_DSN="postgres://panakoes_auth:${NEW_PW}@${NEW_HOST}:5432/panakoes_auth?sslmode=require"

# 2. Migrate data via pg_dump
pg_dump "$OLD_DSN" | psql "$NEW_DSN"

# 3. Verify row counts match in both
psql "$OLD_DSN" -c 'SELECT count(*) FROM "user"; SELECT count(*) FROM session;'
psql "$NEW_DSN" -c 'SELECT count(*) FROM "user"; SELECT count(*) FROM session;'

# 4. Flip the DSN secret to point at RDS
aws secretsmanager put-secret-value \
  --secret-id panakoes-dev/database-url \
  --secret-string "$NEW_DSN"

# 5. Roll the auth ECS service to pick up the new secret
aws ecs update-service \
  --cluster panakoes-dev \
  --service panakoes-dev-auth \
  --force-new-deployment

# 6. Verify against the live SPA
curl -X POST https://<api-gateway>/v1/auth/sign-in -d '{"email":"...","password":"..."}'
# Expected: <500 ms response (no cold-start)
```

### v1 to v2 KMS migration (W2-T5, blue/green snapshot-restore)

The W2-T5 PR adds three Terraform-managed resources (`aws_db_snapshot.pre_migration`,
`aws_db_snapshot_copy.re_encrypted`, `aws_db_instance.auth_db_v2`) which together
produce a NEW RDS instance restored from a re-encrypted snapshot of the v1
instance. The original `aws_db_instance.auth_db` stays unchanged in this PR
because `kms_key_id` is ForceNew and any modification would destroy + recreate
the live instance (losing the user / session tables).

After `terraform apply` of this module:

```bash
# 0. Verify the v2 instance is available and shows the consolidated CMK.
V2_ARN=$(cd infra/dev/auth-db-rds && terraform output -raw instance_arn_v2)
V2_KMS=$(cd infra/dev/auth-db-rds && terraform output -raw kms_key_arn_v2)
aws rds describe-db-instances --db-instance-identifier panakoes-dev-auth-rds-v2 \
  --query 'DBInstances[0].{Status:DBInstanceStatus,KmsKeyId:KmsKeyId,Endpoint:Endpoint.Address}'
# Expected: Status=available, KmsKeyId == V2_KMS, Endpoint == panakoes-dev-auth-rds-v2.*.rds.amazonaws.com

# 1. Construct the v1 and v2 DSNs from Terraform outputs + the live password secret.
PW=$(aws secretsmanager get-secret-value --secret-id panakoes-dev/postgres-auth-db-password --query SecretString --output text)
V1_HOST=$(cd infra/dev/auth-db-rds && terraform output -raw instance_address)
V2_HOST=$(cd infra/dev/auth-db-rds && terraform output -raw instance_address_v2)
V1_DSN="postgres://panakoes_auth:${PW}@${V1_HOST}:5432/panakoes_auth?sslmode=require"
V2_DSN="postgres://panakoes_auth:${PW}@${V2_HOST}:5432/panakoes_auth?sslmode=require"

# 2. Verify v2 row counts match v1. (No pg_dump needed: the snapshot restore
#    already brought the data across. This is the safety check.)
psql "$V1_DSN" -c 'SELECT count(*) FROM "user"; SELECT count(*) FROM session;'
psql "$V2_DSN" -c 'SELECT count(*) FROM "user"; SELECT count(*) FROM session;'
# Expected: identical counts.

# 3. Flip the DSN secret to point at the v2 endpoint.
aws secretsmanager put-secret-value \
  --secret-id panakoes-dev/database-url \
  --secret-string "$V2_DSN"

# 4. Roll the auth ECS service so the new task definition picks up the new
#    secret value at startup. The Better-Auth service does not gracefully
#    rotate Secrets Manager values mid-process; a force-new-deployment is
#    required.
aws ecs update-service \
  --cluster panakoes-dev \
  --service panakoes-dev-auth \
  --force-new-deployment

# 5. Verify sign-in works against the live SPA. Expect ~5 minutes of partial
#    auth unavailability while the rolling task swap completes (one task at
#    a time; circuit_breaker rolls back if any task fails its health check).
curl -X POST https://<api-gateway>/v1/auth/sign-in -d '{"email":"...","password":"..."}'

# 6. Optional but recommended: 24-hour burn-in before W2-T7 retires v1. During
#    burn-in, both instances exist; rollback to v1 is `put-secret-value` with
#    V1_DSN and a second force-new-deployment.
```

**Expected auth outage during cutover:** ~5 minutes, bounded by the ECS rolling
deployment + Better-Auth task startup (Secrets Manager fetch + Drizzle migration
check + Hono server bind). Sign-in attempts during this window may hit a task
still pointing at v1; those attempts succeed against v1 until the task is
drained. The v1 instance accepts reads/writes throughout the cutover; we are
not coordinating a single switchover point.

### Retirement of v1 (W2-T7, separate PR)

The W2-T5 PR intentionally leaves the v1 `aws_db_instance.auth_db` in the
module so the cutover can roll back. Once burn-in confirms v2 is serving all
auth traffic cleanly (recommended 24 hours; minimum 1 hour for dev), the
W2-T7 retirement PR removes:

- `aws_db_instance.auth_db` (the v1 instance, `panakoes-dev-auth-rds`)
- `aws_kms_key.auth_db_rds` (the module-local CMK that encrypted v1's volume)
- `aws_kms_alias.auth_db_rds`
- `aws_db_snapshot.pre_migration` (deleted out of band via
  `aws rds delete-db-snapshot`; Terraform `terraform state rm` to drop the
  state pointer)
- `aws_db_snapshot_copy.re_encrypted` (same handling)

The v2 instance's outputs (`instance_arn_v2`, `instance_endpoint_v2`, etc.)
are reassigned to the legacy unsuffixed names (`instance_arn`,
`instance_endpoint`) at retirement time so downstream consumers (the secrets
module's `database-url` placeholder, future remote_state lookups) keep
working without a coordinated rename.

## Decommissioning Aurora (deferred PR, 7+ days after cutover)

Once burn-in confirms the RDS instance is serving all auth traffic cleanly:

```bash
cd ~/projects/panakoes/infra/dev/auth-db
terraform destroy
git rm -r infra/dev/auth-db/
# Open a PR for the decommission
```

The pre-migration Aurora snapshot (`panakoes-dev-auth-...-pre-rds-migration-<date>`) is retained automatically by RDS until manually deleted, giving an additional safety net for ~1 month of accidental-loss recovery.

## Free Tier eligibility

AWS account created 2026-05-07. Free Tier for RDS db.t4g.micro single-AZ:

- 750 instance-hours/month (covers always-on single instance)
- 20 GB gp2 / gp3 storage
- 20 GB backup storage
- Free for 12 months from account creation → expires ~2027-05-07

Set the `instance_class` variable to anything other than `db.t4g.micro` only if Free Tier is exhausted AND the workload demands it.

## Cost trajectory

| Window | Cost |
|---|---|
| 2026-05 to 2027-05 (Free Tier) | **$0/mo** |
| 2027-05 onward | ~$12-14/mo (db.t4g.micro on-demand pricing in us-east-1) |
| Multi-AZ if flipped on | Doubles instance cost (~$24-28/mo) |

## Related

- `infra/dev/auth-db/` -- the Aurora Serverless v2 module being replaced. Will be removed in a follow-up PR after burn-in.
- `infra/dev/secrets/` -- manages the `postgres-auth-db-password` secret. The DSN secret (`postgres-auth-db-dsn`) is updated manually during cutover, not via Terraform.
- `services/auth/` -- consumes the DSN from Secrets Manager at startup.

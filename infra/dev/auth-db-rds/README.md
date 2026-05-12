# `infra/dev/auth-db-rds/`

RDS PostgreSQL 16 instance (`db.t4g.micro`, single-AZ) backing Better-Auth's tables for the auth microservice in the `dev` environment.

## Why this module exists

Replaces `infra/dev/auth-db/` (Aurora Serverless v2) for the auth-db specifically. The Aurora module remains in the repo for the duration of the burn-in window (7 days post-cutover), then is decommissioned in a follow-up PR.

**Root cause for the migration:** Aurora Serverless v2 with `min_capacity_acu = 0` (the dev cost-saver setting) cold-starts on first sign-in after 5 min idle. Measurement: cold sign-in = 11,963 ms total, of which ~11,500 ms is Aurora's resume-from-pause sequence (confirmed by client curl timing, API Gateway `integrationLatency`, and Aurora `ServerlessDatabaseCapacity` metric all agreeing). See `.agent-runs/2026-05-12T23-25-00Z-auth-coldstart-research.md` for the full investigation.

**Why RDS not Aurora keep-warm:** the workload (tiny storage, <5 QPS sustained, bursty at sign-in) doesn't use any Aurora-specific capability (auto-scaling storage, multi-AZ replication, read replicas). Aurora at `min_capacity_acu = 0.5` (no cold start) would cost ~$43/mo for capabilities the auth-db doesn't need. RDS db.t4g.micro is always-on, $0/mo for 12 months on AWS Free Tier (then ~$12/mo), no cold-start.

## Cutover sequence (one-time, manual)

After `terraform apply` provisions the instance:

```bash
# 1. Get both DSNs
OLD_DSN=$(aws secretsmanager get-secret-value --secret-id panakoes-dev/postgres-auth-db-dsn --query SecretString --output text)
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
  --secret-id panakoes-dev/postgres-auth-db-dsn \
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

# Aurora Restore Drill Runbook

## Purpose

Validate that the dev Aurora Serverless v2 cluster's automated backups
(point-in-time recovery, retention 7 days) actually produce a usable
restore. Catches policy drift, KMS access gaps, IAM regressions, and
parameter-group mismatches in a controlled drill, well before an
incident forces the same procedure under time pressure.

Run this drill quarterly per `infra/dev/backup/README.md` and after any
of: KMS key policy change touching `arn:aws:kms:us-east-1:659225405128:key/46088235-9f1e-4ccc-83a5-db5e5e495525`,
auth-db IAM role change, subnet-group rewrite, or Aurora engine major
bump.

## When to use this runbook

- Scheduled quarterly drill (calendar reminder).
- After a change to the auth-db KMS CMK, subnet group, security group,
  or master-password rotation procedure.
- After a change to the AWS Backup vault / plan in `infra/dev/backup/`
  (even though the dev cluster currently relies on Aurora native PITR,
  not AWS Backup; see "What this drill validates" below).
- Before a production cluster's first apply, to rehearse the procedure
  against a known-recoverable dev cluster.

For an actual incident (corruption, accidental DROP, ransomware in
dev), follow `docs/runbooks/disaster-recovery.md` Lane B (RDS Postgres
PITR), which references this runbook for the restore-command details.

## What this drill validates

- Aurora native automated backups + PITR for the dev auth cluster
  (`panakoes-dev-auth-20260510055543895900000001`, retention 7 days,
  earliest restorable rolls forward continuously).
- `restore-db-cluster-to-point-in-time` succeeds end to end against
  the live KMS CMK, subnet group, and VPC SG.
- The restored cluster contains the same data as production at the
  restore timestamp (row-count parity on the `user` table).
- The restored cluster accepts the master credentials currently held
  in `panakoes-dev/postgres-auth-db-password`. This is only true when
  the password was last rotated BEFORE the earliest restorable time.
  If rotation falls inside the PITR window, the restored cluster
  holds the older hash and this probe will fail with `password
  authentication failed`; in that case, read the historical secret
  version with `aws secretsmanager get-secret-value --version-id
  <prior>` and rerun the probe.

## What this drill does NOT cover

- **Account-level disaster** (account compromise, region-wide AWS
  outage, accidental account deletion). Aurora PITR + AWS Backup both
  live in the same account and region; a compromised root or a
  us-east-1 outage takes both down. Follow-up: stand up
  cross-region AWS Backup copy (us-west-2) for the auth cluster
  before production launch; cross-account vault copy for the
  production cluster.
- **AWS Backup vault recovery points.** The `panakoes-dev` vault
  currently holds 6 DynamoDB recovery points and 0 Aurora recovery
  points; the dev backup plan in `infra/dev/backup/main.tf` selects
  only the three DynamoDB tables. Restoring an Aurora recovery point
  from AWS Backup is a separate procedure (`aws backup
  start-restore-job`) and is not exercised by this runbook. Add the
  auth cluster ARN to `aws_backup_selection.resources` to bring it
  under AWS Backup; until then, PITR via the RDS native API is the
  only restore path for the cluster.
- **Logical-only corruption that predates the PITR window** (older
  than 7 days). Recover via the most-recent monthly snapshot once the
  cluster is added to the AWS Backup plan; until then, accept the
  7-day cap.
- **Application-level state outside the database** (Secrets Manager
  contents, S3 objects, ECS task definitions). Each has its own
  recovery path documented elsewhere.

## Prerequisites

- AWS CLI authenticated against the Panakoes dev account
  (`aws sts get-caller-identity --profile panakoes-admin` returns
  `659225405128`).
- Cluster + subnet group + SG + KMS CMK from `infra/dev/auth-db/`
  applied (see `infra/dev/auth-db/README.md`).
- `panakoes-dev/postgres-auth-db-password` populated.
- ECS cluster `panakoes-dev` available for the verification task run.
- IAM role `panakoes-dev-auth-execution` exists (created by
  `infra/dev/iam/`); the probe task definition reuses it for ECR pull
  + log writes.
- Log group `/panakoes/dev/auth` exists.
- Budget: under $0.50 per drill (PITR clone of an idle cluster runs
  ~10 minutes at 0.5 to 2 ACU; Fargate probe is two ~1-minute tasks
  at 256 CPU / 512 MiB).

## Pre-flight

1. **Confirm AWS Backup vault state.**

   ```bash
   aws --profile panakoes-admin backup list-backup-vaults --region us-east-1 \
     --query 'BackupVaultList[?BackupVaultName==`panakoes-dev`].{name:BackupVaultName,points:NumberOfRecoveryPoints}'
   ```

   Expect the `panakoes-dev` vault to exist. Note the recovery-point
   count for the post-drill report.

2. **Confirm Aurora PITR + retention.**

   ```bash
   aws --profile panakoes-admin rds describe-db-clusters --region us-east-1 \
     --db-cluster-identifier panakoes-dev-auth-20260510055543895900000001 \
     --query 'DBClusters[0].{retention:BackupRetentionPeriod,window:PreferredBackupWindow,earliest:EarliestRestorableTime,latest:LatestRestorableTime,status:Status}'
   ```

   Required: `retention >= 7`, `status == "available"`, `LatestRestorableTime`
   is within the last 5 minutes. `LatestRestorableTime` lagging more
   than 10 minutes is itself an investigation trigger.

3. **Confirm master password's `LastChangedDate` predates
   `EarliestRestorableTime`.**

   ```bash
   aws --profile panakoes-admin secretsmanager describe-secret --region us-east-1 \
     --secret-id panakoes-dev/postgres-auth-db-password \
     --query '{lastChanged:LastChangedDate}'
   ```

   If `LastChangedDate` is newer than `EarliestRestorableTime`, fetch
   the historical version id from `aws secretsmanager list-secret-version-ids`
   and use the matching `--version-id <id>` in the probe step.

## Procedure

1. **Pick a test cluster name and a restore timestamp.**

   ```bash
   TS=$(date -u +%Y%m%d%H%M%S)
   TEST_CLUSTER="panakoes-dev-auth-restore-test-$TS"
   echo "$TEST_CLUSTER"
   ```

   The timestamp suffix prevents collision with prior aborted drills.

2. **Restore the cluster to the latest restorable time.**

   ```bash
   aws --profile panakoes-admin rds restore-db-cluster-to-point-in-time \
     --region us-east-1 \
     --db-cluster-identifier "$TEST_CLUSTER" \
     --source-db-cluster-identifier panakoes-dev-auth-20260510055543895900000001 \
     --use-latest-restorable-time \
     --db-subnet-group-name panakoes-dev-auth-db \
     --vpc-security-group-ids sg-05d00ec8d4a5df12a \
     --kms-key-id arn:aws:kms:us-east-1:659225405128:key/46088235-9f1e-4ccc-83a5-db5e5e495525 \
     --no-deletion-protection \
     --serverless-v2-scaling-configuration MinCapacity=0.5,MaxCapacity=2 \
     --tags Key=Purpose,Value=restore-drill
   ```

   Notes:
   - Do NOT pass `--engine`; the parser collides with `--engine-mode`
     and Aurora infers engine from the source cluster.
   - Do NOT pass both `--restore-to-time` and
     `--use-latest-restorable-time`; pick one. Latest-restorable is
     the closest-to-now approximation and what an incident response
     would use.
   - `--no-deletion-protection` is required so the cleanup step can
     delete the cluster without a manual `modify-db-cluster` first.
   - `MaxCapacity=2` instead of the production `4` bounds the drill's
     cost ceiling; the probe query never scales the cluster anyway.

3. **Create a writer instance for the restored cluster.**

   Aurora's restore API creates only the cluster volume; the writer
   instance is a separate call.

   ```bash
   aws --profile panakoes-admin rds create-db-instance \
     --region us-east-1 \
     --db-instance-identifier "$TEST_CLUSTER-writer" \
     --db-cluster-identifier "$TEST_CLUSTER" \
     --db-instance-class db.serverless \
     --engine aurora-postgresql
   ```

4. **Wait for cluster + instance to be `available`.**

   ```bash
   for i in $(seq 1 30); do
     CSTATUS=$(aws --profile panakoes-admin rds describe-db-clusters --region us-east-1 \
       --db-cluster-identifier "$TEST_CLUSTER" \
       --query 'DBClusters[0].Status' --output text)
     ISTATUS=$(aws --profile panakoes-admin rds describe-db-instances --region us-east-1 \
       --db-instance-identifier "$TEST_CLUSTER-writer" \
       --query 'DBInstances[0].DBInstanceStatus' --output text)
     echo "[$(date -u +%H:%M:%S)] cluster=$CSTATUS instance=$ISTATUS"
     [ "$CSTATUS" = "available" ] && [ "$ISTATUS" = "available" ] && break
     sleep 30
   done
   ```

   Expected timing: cluster reaches `available` in ~4 minutes; the
   writer instance takes another ~7 to 10 minutes (creates +
   configures-enhanced-monitoring + available). Total ~10 to 14
   minutes on the 2026-05-11 baseline drill.

5. **Verify with the probe task.**

   Register a one-shot Fargate task that runs `psql -c 'SELECT COUNT(*)
   FROM "user"'` against the restored cluster, then run it on the dev
   ECS cluster.

   ```bash
   ENDPOINT="$TEST_CLUSTER.cluster-cm18asy2wvdl.us-east-1.rds.amazonaws.com"
   PROD_PW=$(aws --profile panakoes-admin secretsmanager get-secret-value \
     --region us-east-1 \
     --secret-id panakoes-dev/postgres-auth-db-password \
     --query 'SecretString' --output text)

   cat > /tmp/probe-td.json <<JSON
   {
     "family": "panakoes-restore-drill-probe",
     "networkMode": "awsvpc",
     "requiresCompatibilities": ["FARGATE"],
     "cpu": "256",
     "memory": "512",
     "executionRoleArn": "arn:aws:iam::659225405128:role/panakoes-dev-auth-execution",
     "containerDefinitions": [{
       "name": "psql",
       "image": "public.ecr.aws/docker/library/postgres:16-alpine",
       "essential": true,
       "command": ["sh","-c","PGPASSWORD=\"\$PGPASSWORD\" psql -h \"\$PGHOST\" -U panakoes_auth -d panakoes_auth -p 5432 -c 'SELECT COUNT(*) AS row_count FROM \"user\";' && echo DRILL_QUERY_OK"],
       "environment": [
         {"name": "PGHOST", "value": "$ENDPOINT"},
         {"name": "PGPASSWORD", "value": "$PROD_PW"}
       ],
       "logConfiguration": {
         "logDriver": "awslogs",
         "options": {
           "awslogs-group": "/panakoes/dev/auth",
           "awslogs-region": "us-east-1",
           "awslogs-stream-prefix": "restore-drill"
         }
       }
     }]
   }
   JSON

   aws --profile panakoes-admin ecs register-task-definition \
     --region us-east-1 \
     --cli-input-json file:///tmp/probe-td.json \
     --query 'taskDefinition.taskDefinitionArn'

   TASK_ARN=$(aws --profile panakoes-admin ecs run-task --region us-east-1 \
     --cluster panakoes-dev \
     --task-definition panakoes-restore-drill-probe \
     --launch-type FARGATE \
     --network-configuration "awsvpcConfiguration={subnets=[subnet-0569c7f8ed0bd37f4,subnet-077b6d21274538423,subnet-03d396f07050b97a0],securityGroups=[sg-05d00ec8d4a5df12a],assignPublicIp=DISABLED}" \
     --query 'tasks[0].taskArn' --output text)
   TASK_ID=${TASK_ARN##*/}
   ```

   Wait for the task to stop, then pull the log:

   ```bash
   for i in $(seq 1 30); do
     S=$(aws --profile panakoes-admin ecs describe-tasks --region us-east-1 \
       --cluster panakoes-dev --tasks $TASK_ID \
       --query 'tasks[0].lastStatus' --output text)
     echo "[$(date +%H:%M:%S)] $S"
     [ "$S" = "STOPPED" ] && break
     sleep 15
   done

   aws --profile panakoes-admin logs get-log-events --region us-east-1 \
     --log-group-name /panakoes/dev/auth \
     --log-stream-name "restore-drill/psql/$TASK_ID" \
     --query 'events[].message' --output text
   ```

6. **Compare against the production cluster.**

   Rerun the probe task with `PGHOST` pointing at the source cluster
   endpoint (`panakoes-dev-auth-20260510055543895900000001.cluster-cm18asy2wvdl.us-east-1.rds.amazonaws.com`)
   and a stream prefix of `prod-baseline`. The two row counts must
   match.

## Verification

- Probe task exits 0 against the restored cluster.
- Log contains `row_count` followed by an integer and `DRILL_QUERY_OK`.
- Probe task exits 0 against the production cluster.
- The two `row_count` values are equal.

If row counts differ by 1 to 2, account for in-flight inserts between
the `--use-latest-restorable-time` snapshot and the prod-baseline
probe. If they differ by more, investigate before declaring success
(possible causes: wrong source cluster, restore timestamp anchored to
a stale boundary, prod cluster mid-migration).

## Cleanup (mandatory)

Aurora Serverless v2 idle costs $0.06/hour per ACU; a forgotten test
cluster runs ~$1/day. Delete immediately on success.

1. **Delete the writer instance (skip final snapshot).**

   ```bash
   aws --profile panakoes-admin rds delete-db-instance --region us-east-1 \
     --db-instance-identifier "$TEST_CLUSTER-writer" \
     --skip-final-snapshot
   ```

   Wait until `DBInstanceNotFound`:

   ```bash
   for i in $(seq 1 30); do
     S=$(aws --profile panakoes-admin rds describe-db-instances --region us-east-1 \
       --db-instance-identifier "$TEST_CLUSTER-writer" \
       --query 'DBInstances[0].DBInstanceStatus' --output text 2>&1)
     echo "[$(date +%H:%M:%S)] $S"
     echo "$S" | grep -q "DBInstanceNotFound" && break
     sleep 20
   done
   ```

   Instance deletion takes ~8 to 9 minutes for Serverless v2.

2. **Delete the cluster (skip final snapshot).**

   ```bash
   aws --profile panakoes-admin rds delete-db-cluster --region us-east-1 \
     --db-cluster-identifier "$TEST_CLUSTER" \
     --skip-final-snapshot
   ```

   `--skip-final-snapshot` is acceptable HERE ONLY because the cluster
   is a throwaway clone of production. Never pass `--skip-final-snapshot`
   against the live `panakoes-dev-auth-*` source cluster.

3. **Deregister the probe task definitions.**

   ```bash
   aws --profile panakoes-admin ecs deregister-task-definition --region us-east-1 \
     --task-definition panakoes-restore-drill-probe:<revision>
   aws --profile panakoes-admin ecs deregister-task-definition --region us-east-1 \
     --task-definition panakoes-restore-drill-probe-prod:<revision>
   ```

4. **Sanity check: no `restore-test` clusters remain.**

   ```bash
   aws --profile panakoes-admin rds describe-db-clusters --region us-east-1 \
     --query 'DBClusters[?contains(DBClusterIdentifier,`restore-test`)].DBClusterIdentifier'
   ```

   Output must be `[]`.

## Cost expectations

| Resource                                          | Drill cost  |
|---------------------------------------------------|-------------|
| Aurora Serverless v2 clone, 0.5 ACU idle, ~25 min | ~$0.03      |
| KMS request charges (snapshot decrypt + restore)  | ~$0.01      |
| Fargate probe x2 (256/512, ~1 min each)           | ~$0.01      |
| CloudWatch Logs ingestion (small)                 | <$0.01      |

Total: well under $0.10 per drill. The $0.50 budget cap is the hard
ceiling for cost overruns from a stuck cluster delete.

## Expected timings

| Step                              | Duration       |
|-----------------------------------|----------------|
| Cluster restore (volume creation) | 4 min          |
| Writer instance creation          | 7 to 10 min    |
| Probe task (restored + prod)      | 1 to 2 min each |
| Cluster + instance delete         | 9 to 12 min    |
| **Total wall clock**              | ~25 to 30 min  |

## Disaster scenarios this drill covers

- **Accidental `DROP TABLE` or `DELETE` without `WHERE`** on the
  `user`, `session`, `account`, or `verification` tables. Restore to
  a timestamp before the destructive statement, dump the affected
  table from the restored cluster (`pg_dump -t '"user"'`), restore
  into the production cluster.
- **Ransomware / malicious tampering** with row contents in the
  PITR window. Same restore + targeted dump procedure; combine with
  a `services/auth` rotate-all-sessions step.
- **Schema migration corruption.** If `drizzle-kit push` lands a
  destructive migration, restore to the timestamp before the
  migration runner started, dump the affected tables, replay them
  into a re-migrated production cluster.

## Disaster scenarios this drill does NOT cover

- **Region outage in us-east-1.** Both source and restore live in the
  same region. Follow-up: add cross-region AWS Backup copy to
  us-west-2; production cluster only.
- **Account compromise.** Both source and restore live in the same
  account; an attacker with admin can delete both. Follow-up: add
  cross-account AWS Backup copy into a dedicated backup-vault account
  for production.
- **Corruption that pre-dates the 7-day PITR window.** Mitigated by
  the monthly snapshot rule in `infra/dev/backup/` once the auth
  cluster is added to the backup selection.

## Rollback

This drill is non-destructive against the source cluster (PITR is
read-only against the source's archived log stream). If the drill
fails partway:

1. If the restore created a cluster + instance, run the cleanup steps
   above to remove them.
2. If the probe task is stuck (Fargate provisioning failure), stop
   it: `aws ecs stop-task --cluster panakoes-dev --task <id>`.
3. The source cluster is untouched; production keeps running.

## References

- `infra/dev/auth-db/README.md`: source cluster module, KMS, subnet
  group, sizing.
- `infra/dev/backup/README.md`: AWS Backup vault + plan (currently
  DynamoDB-only; expansion to Aurora is a follow-up).
- `docs/runbooks/disaster-recovery.md` Lane B: incident-time PITR
  procedure for the auth cluster (this runbook is the drill
  rehearsal of that procedure).
- ADR-036 in `docs/adr/`: Aurora Serverless v2 scale-to-zero
  decision and operational posture.
- ADR-039 in `docs/adr/`: auth-db application role + migration
  runner; relevant when the restored data needs the `auth_app` role
  granted before application use.

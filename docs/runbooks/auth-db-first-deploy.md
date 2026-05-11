# Auth DB first-deploy

## Purpose

Walk a single operator from "Aurora cluster exists, auth ECS service exists,
no tables yet" to "auth service is live against the cluster, signed up its
first user, and is running as a least-privileged DB role." Applies the
credential split + operator-invoked migration posture defined in
[ADR-039](../adr/ADR-039-auth-db-application-role-and-migration-runner.md).

This is a one-time per-environment procedure. After it completes, ongoing
schema changes use only steps 2 and 4 + 5 + 8 (push a new image, run the
migrator, force-redeploy).

## When to use this runbook

- The dev (or any new) Aurora cluster has just been applied and contains
  zero application tables.
- A new ECR image of the auth service that includes `dist/migrate.js` has
  just been pushed (verify with `aws ecr describe-images --repository-name
  panakoes-dev-auth`).
- The `panakoes-dev/database-url` secret in Secrets Manager is still the
  Terraform-provisioned placeholder (no `auth_app` credentials in it yet).

For routine schema-change-only operations on an already-deployed cluster,
skip to the "Routine migration" lane near the bottom.

## Prerequisites

| Tool | Verification |
|---|---|
| `aws` CLI v2 | `aws --version` shows 2.x |
| `jq` | `jq --version` |
| `terraform` >= 1.6 | `terraform version` |
| `openssl` | `openssl version` |
| `psql` (any version 13+ for client-side; cluster is server 16) | only used inside one-off tasks; optional locally |
| `AWS_PROFILE=panakoes-admin` configured | `aws sts get-caller-identity --profile panakoes-admin` returns account `659225405128` |

All AWS CLI calls below assume `AWS_PROFILE=panakoes-admin` and `AWS_REGION=us-east-1`
unless otherwise noted. Export them once at shell start:

```bash
export AWS_PROFILE=panakoes-admin
export AWS_REGION=us-east-1
```

## Cluster reference data (dev)

The procedure uses the dev Aurora cluster created by `infra/dev/auth-db/`.
Recorded for fast copy-paste:

| Field | Value |
|---|---|
| Cluster identifier | `panakoes-dev-auth-20260510055543895900000001` |
| Writer endpoint | `panakoes-dev-auth-20260510055543895900000001.cluster-cm18asy2wvdl.us-east-1.rds.amazonaws.com` |
| Port | `5432` |
| Master username | `panakoes_auth` |
| Database name | `panakoes_auth` |
| Master password secret | `panakoes-dev/postgres-auth-db-password` |
| Runtime DATABASE_URL secret | `panakoes-dev/database-url` (placeholder until step 7) |
| Migrate DATABASE_URL secret | `panakoes-dev/database-url-migrate` (created in step 3) |

## Procedure

### 1. Pre-flight checks

Why: every subsequent step assumes these three preconditions. Catching a
miss now saves a 90-second Fargate task launch later.

```bash
# Aurora cluster status (expect "available")
aws rds describe-db-clusters \
  --db-cluster-identifier panakoes-dev-auth-20260510055543895900000001 \
  --query 'DBClusters[0].Status'

# Master password secret populated (expect a non-empty CreatedDate; do NOT
# print the secret value)
aws secretsmanager describe-secret \
  --secret-id panakoes-dev/postgres-auth-db-password \
  --query 'CreatedDate'

# Auth image tag is in ECR with dist/migrate.js (replace TAG with the tag
# the operator just pushed)
TAG=<tag-you-pushed>
aws ecr describe-images \
  --repository-name panakoes-dev-auth \
  --image-ids imageTag=$TAG \
  --query 'imageDetails[0].imagePushedAt'
```

Expected outcomes: `"available"`, a recent ISO timestamp, a recent ISO
timestamp. If any fail, stop here and fix.

### 2. Register the new auth task definition revision

Why: the auth service must be running the image that contains
`dist/migrate.js` so the one-off run-task in step 5 has the binary to invoke.
We change `var.auth_image_tag` and re-apply `infra/dev/ecs/`. The service
itself can stay on the older revision; the run-task picks up the latest
revision automatically (see `scripts/run-auth-migration.sh`, which reads
the current service's task definition).

```bash
cd infra/dev/ecs
# Option A: bump the default in variables.tf and commit. Option B (used here
# for the runbook): override at plan time.
TF_VAR_auth_image_tag=$TAG terraform plan -out=plan.out

# Expected plan shape:
#   ~ aws_ecs_task_definition.auth   image -> .../panakoes-dev-auth:$TAG
#   No changes to networking, IAM, or other services.

TF_VAR_auth_image_tag=$TAG terraform apply plan.out
```

If you intend the new tag to be the persistent default, also bump
`infra/dev/ecs/variables.tf` `auth_image_tag.default = "$TAG"` in a follow-up
`chore(infra)` PR. The runbook does not require it.

### 3. Create the migrate-time `DATABASE_URL` secret

Why: ADR-039 splits DDL credentials from DML credentials. The migration runs
as the master user (`panakoes_auth`), which already exists, and reads its
connection string from a new secret distinct from the runtime
`panakoes-dev/database-url`. We never overwrite the runtime secret with
master-user credentials, even temporarily; that would briefly run the auth
service as DB owner during step 8 if anything raced.

```bash
# Pull the master password out of its secret. Do NOT echo $MASTER_PW.
MASTER_PW=$(aws secretsmanager get-secret-value \
  --secret-id panakoes-dev/postgres-auth-db-password \
  --query SecretString --output text)

CLUSTER_ENDPOINT=panakoes-dev-auth-20260510055543895900000001.cluster-cm18asy2wvdl.us-east-1.rds.amazonaws.com

# Create the new secret. URL-encode the password defensively in case it
# contains reserved chars (jq's @uri filter handles this).
MIGRATE_URL=$(printf '%s' "$MASTER_PW" | jq -Rr \
  --arg user panakoes_auth \
  --arg host "$CLUSTER_ENDPOINT" \
  --arg db panakoes_auth \
  '@uri as $pw | "postgres://" + $user + ":" + $pw + "@" + $host + ":5432/" + $db')

aws secretsmanager create-secret \
  --name panakoes-dev/database-url-migrate \
  --description "DDL credential (master user) for auth-db migrations. Read only by one-off ECS run-task; never by the running auth service. See ADR-039." \
  --secret-string "$MIGRATE_URL"

unset MASTER_PW MIGRATE_URL
```

If the secret already exists (re-running this procedure on a new tag),
use `put-secret-value` instead:

```bash
aws secretsmanager put-secret-value \
  --secret-id panakoes-dev/database-url-migrate \
  --secret-string "$MIGRATE_URL"
```

Capture the new secret's ARN; you will need it in step 4.

```bash
MIGRATE_SECRET_ARN=$(aws secretsmanager describe-secret \
  --secret-id panakoes-dev/database-url-migrate \
  --query ARN --output text)
echo "$MIGRATE_SECRET_ARN"
```

### 4. Override `DATABASE_URL` for the migration task

Why: the auth task definition wires `DATABASE_URL` from
`panakoes-dev/database-url` (the runtime secret, still a placeholder). The
migrator needs the master credentials, which live in
`panakoes-dev/database-url-migrate`. ECS `run-task` cannot rewrite a
secret-backed env var via `--overrides` (secrets are resolved by the
execution role before the container starts). Two paths:

**Path A (recommended): briefly swap the runtime secret's value.** Do this
only inside a single 60-second window between starting the migration task
and step 7 where you re-populate the runtime secret with `auth_app`
credentials. The migration container is the only thing that boots in this
window; the running auth service tasks already have the previous secret
value cached in env (ECS resolves secrets once at task start, per
container, not on every read).

Skipped for first-deploy because the runtime secret is still a placeholder
that no service has booted against. Use path B.

**Path B (used for first-deploy): grant the execution role read on the new
secret, register a one-off task definition revision that points the
container's `DATABASE_URL` secret at `database-url-migrate`, run-task it,
deregister.**

The auth execution role's secrets policy is provisioned in `infra/dev/iam/`
with a list of allowed secret ARNs. If
`panakoes-dev/database-url-migrate` is not in that list, the run-task will
fail at startup with `ResourceInitializationError: secret not authorized`.
Confirm and patch if needed:

```bash
aws iam get-role-policy \
  --role-name panakoes-dev-auth-execution \
  --policy-name execution-secrets \
  --query 'PolicyDocument.Statement[].Resource' --output json
```

If the migrate-secret ARN is absent, add it via a `chore(infra)` PR to
`infra/dev/iam/` (one-line addition to the auth execution-role's secret-ARN
allowlist), `terraform apply`, then continue.

The simplest one-off task definition is a clone of the current auth task
def with the one secret swap. Use `aws ecs describe-task-definition`,
`jq` the JSON down, and `register-task-definition`:

```bash
CURRENT_TD=$(aws ecs describe-services \
  --cluster panakoes-dev --services panakoes-dev-auth \
  --query 'services[0].taskDefinition' --output text)

aws ecs describe-task-definition --task-definition "$CURRENT_TD" \
  --query taskDefinition --output json \
  | jq --arg arn "$MIGRATE_SECRET_ARN" '
      .containerDefinitions[0].secrets |= map(
        if .name == "DATABASE_URL" then .valueFrom = $arn else . end)
      | {family, taskRoleArn, executionRoleArn, networkMode, containerDefinitions,
         volumes, placementConstraints, requiresCompatibilities, cpu, memory,
         runtimePlatform, ephemeralStorage}
    ' > migrate-td.json

MIGRATE_TD_ARN=$(aws ecs register-task-definition \
  --cli-input-json file://migrate-td.json \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "$MIGRATE_TD_ARN"
```

This registers a new revision under the same family (e.g.
`panakoes-dev-auth:42`) with `DATABASE_URL` rewired. The running service
keeps using its current revision; only the one-off task uses the migrate
revision.

### 5. Run the migrator

Why: applies `drizzle/0000_initial.sql` (the four Better-Auth tables) and
`drizzle/0001_add_role.sql` (the `role` column + CHECK constraint), records
both in the `__migrations` table for idempotency.

```bash
# Override the run-auth-migration.sh defaults to use the migrate task-def
# revision rather than the running service's revision. The wrapper script
# reads $SERVICE to discover networking and the task def. We bypass the
# task-def-from-service discovery by editing one line of the script flow:
# easier path is a direct `aws ecs run-task`.

# Discover network config from the running service.
SVC=$(aws ecs describe-services --cluster panakoes-dev --services panakoes-dev-auth \
  --query 'services[0]' --output json)
SUBNETS=$(echo "$SVC" | jq -r '.networkConfiguration.awsvpcConfiguration.subnets | join(",")')
SGS=$(echo "$SVC" | jq -r '.networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')
CONTAINER_NAME=$(aws ecs describe-task-definition --task-definition "$MIGRATE_TD_ARN" \
  --query 'taskDefinition.containerDefinitions[0].name' --output text)

OVERRIDES=$(jq -nc --arg name "$CONTAINER_NAME" \
  '{containerOverrides: [{name: $name, command: ["node", "dist/migrate.js"]}]}')
NETCFG=$(jq -nc --arg subnets "$SUBNETS" --arg sgs "$SGS" \
  '{awsvpcConfiguration: {subnets: ($subnets|split(",")), securityGroups: ($sgs|split(",")), assignPublicIp: "DISABLED"}}')

TASK_ARN=$(aws ecs run-task \
  --cluster panakoes-dev \
  --task-definition "$MIGRATE_TD_ARN" \
  --launch-type FARGATE \
  --platform-version LATEST \
  --count 1 \
  --started-by "auth-db-first-deploy-$(date -u +%Y%m%dT%H%M%SZ)" \
  --network-configuration "$NETCFG" \
  --overrides "$OVERRIDES" \
  --query 'tasks[0].taskArn' --output text)

# Wait for the task to stop, then print the logs.
aws ecs wait tasks-stopped --cluster panakoes-dev --tasks "$TASK_ARN"

TASK_ID="${TASK_ARN##*/}"
LOG_GROUP=$(aws ecs describe-task-definition --task-definition "$MIGRATE_TD_ARN" \
  --query 'taskDefinition.containerDefinitions[0].logConfiguration.options."awslogs-group"' --output text)
LOG_PREFIX=$(aws ecs describe-task-definition --task-definition "$MIGRATE_TD_ARN" \
  --query 'taskDefinition.containerDefinitions[0].logConfiguration.options."awslogs-stream-prefix"' --output text)
aws logs get-log-events \
  --log-group-name "$LOG_GROUP" \
  --log-stream-name "${LOG_PREFIX}/${CONTAINER_NAME}/${TASK_ID}" \
  --start-from-head --query 'events[].message' --output text

EXIT_CODE=$(aws ecs describe-tasks --cluster panakoes-dev --tasks "$TASK_ARN" \
  --query 'tasks[0].containers[0].exitCode' --output text)
echo "exit_code=$EXIT_CODE"
```

Expected log shape (one JSON line per migration):

```
{"level":"info","msg":"migration_applied","file":"0000_initial.sql"}
{"level":"info","msg":"migration_applied","file":"0001_add_role.sql"}
{"level":"info","msg":"migration_run_complete","file":"2 applied, 0 skipped"}
```

Expected `exit_code=0`. If non-zero, jump to "Rollback" and re-investigate.

### 6. Create the `auth_app` role + grants

Why: ADR-039 mandates the running service connects as a least-privileged
role with `SELECT, INSERT, UPDATE, DELETE` on the four Better-Auth tables
only. We create the role now (master credentials still in
`database-url-migrate`) and capture its password locally to write into
`panakoes-dev/database-url` in step 7.

Generate the password locally; never log it.

```bash
APP_PW=$(openssl rand -base64 48 | tr -d '/+=' | head -c 32)
# Do NOT echo $APP_PW.
```

Run the role + grant SQL via a second one-off run-task. We reuse the
migrate task definition revision (it has the master credentials wired)
and override the command to run `psql` against `$DATABASE_URL`. The auth
runtime image is `node:22-slim`-based and does not ship `psql`; install
on-demand or use a `postgres:16-alpine` one-off task. The latter is
cleaner because it avoids touching the auth image's package surface:

```bash
# Discover the same network config as step 5. Use the migrate secret as
# the source of DATABASE_URL via env, not via a task-def secret, so we do
# not need to register a third task def. Pass the password as a command
# argument over the wire is unsafe; better is to put it in an env var
# that aws CLI sources from a local file.

# Build the SQL into a heredoc that uses a psql variable so the password
# never lands in shell history or in the run-task overrides JSON. We will
# stream the SQL via stdin to psql inside the container.

cat > /tmp/auth_app_grants.sql <<'SQL'
\set ON_ERROR_STOP on
CREATE ROLE auth_app WITH LOGIN PASSWORD :'app_pw';
GRANT USAGE ON SCHEMA public TO auth_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "user", "session", "account", "verification" TO auth_app;
-- No GRANT on __migrations. No CREATE on schema public.
SQL
```

Easiest invocation pattern: open an interactive psql session from inside
a temporary ECS Exec into a stopped sandbox task, or run a one-off
postgres-client task that pipes the SQL in. The minimal version using
the auth container itself (which has access to the DB) and stdin-streamed
SQL via `node -e`:

```bash
# Run a one-off auth task with command override "node -e ..." that uses
# postgres-js to execute the grants. Pass APP_PW via an env override.
GRANT_JS=$(cat <<'JS'
import postgres from "postgres";
const sql = postgres(process.env.DATABASE_URL, {max:1, prepare:false});
const pw = process.env.APP_PW;
await sql.unsafe(`CREATE ROLE auth_app WITH LOGIN PASSWORD '${pw.replace(/'/g, "''")}'`);
await sql.unsafe(`GRANT USAGE ON SCHEMA public TO auth_app`);
await sql.unsafe(`GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "user", "session", "account", "verification" TO auth_app`);
await sql.end();
console.log(JSON.stringify({level:"info",msg:"auth_app_created"}));
JS
)

OVERRIDES=$(jq -nc \
  --arg name "$CONTAINER_NAME" \
  --arg js "$GRANT_JS" \
  --arg pw "$APP_PW" \
  '{containerOverrides: [{
      name: $name,
      command: ["node", "--input-type=module", "-e", $js],
      environment: [{name: "APP_PW", value: $pw}]
   }]}')

TASK_ARN=$(aws ecs run-task \
  --cluster panakoes-dev \
  --task-definition "$MIGRATE_TD_ARN" \
  --launch-type FARGATE --platform-version LATEST --count 1 \
  --started-by "auth-app-role-grant-$(date -u +%Y%m%dT%H%M%SZ)" \
  --network-configuration "$NETCFG" \
  --overrides "$OVERRIDES" \
  --query 'tasks[0].taskArn' --output text)

aws ecs wait tasks-stopped --cluster panakoes-dev --tasks "$TASK_ARN"
# (tail logs + check exit_code identically to step 5)
```

Why the inline JS path: `psql` is not in the auth image; adding it just
for one grant call expands the runtime image's package surface. The auth
image already has `postgres-js`, so a 10-line script with a parameterized
grant is the smaller diff. The password is passed via an env override
(not as a shell argument) so it does not appear in process listings inside
the container.

Verify the grant set landed cleanly. The simplest verification is a
second one-off task that runs `SELECT` against `information_schema.role_table_grants`:

```bash
VERIFY_JS=$(cat <<'JS'
import postgres from "postgres";
const sql = postgres(process.env.DATABASE_URL, {max:1, prepare:false});
const rows = await sql`
  SELECT table_name, privilege_type
  FROM information_schema.role_table_grants
  WHERE grantee = 'auth_app'
  ORDER BY table_name, privilege_type
`;
console.log(JSON.stringify({level:"info",msg:"auth_app_grants",rows}));
await sql.end();
JS
)
# run-task with this override; expect 16 rows (4 tables x 4 privileges).
```

Expected output: four privileges (`SELECT, INSERT, UPDATE, DELETE`) on
each of `user`, `session`, `account`, `verification`. No grants on
`__migrations` or any other table.

### 7. Populate `panakoes-dev/database-url` with auth_app credentials

Why: this is the secret the runtime auth task reads as `DATABASE_URL` on
its next launch. Writing the `auth_app` connection string here transitions
the service from "owner credentials cached in env (never; placeholder)" to
"least-privileged credentials" on the next deployment.

```bash
APP_URL=$(printf '%s' "$APP_PW" | jq -Rr \
  --arg user auth_app \
  --arg host "$CLUSTER_ENDPOINT" \
  --arg db panakoes_auth \
  '@uri as $pw | "postgres://" + $user + ":" + $pw + "@" + $host + ":5432/" + $db')

aws secretsmanager put-secret-value \
  --secret-id panakoes-dev/database-url \
  --secret-string "$APP_URL"

unset APP_PW APP_URL
```

### 8. Force-redeploy the auth ECS service

Why: ECS resolves secrets at container start, not on every read. The
running auth tasks (if any are up) are still using the previous
placeholder. A force-new-deployment rolls fresh tasks that pick up the
new `database-url` value and connect as `auth_app`.

```bash
aws ecs update-service \
  --cluster panakoes-dev \
  --service panakoes-dev-auth \
  --force-new-deployment

# Watch the rollout complete (typically 60-120s on Fargate).
aws ecs wait services-stable \
  --cluster panakoes-dev \
  --services panakoes-dev-auth
```

Expected: the service reaches `runningCount = desiredCount` with the new
deployment.

### 9. Smoke test

Why: confirms the end-to-end path (API Gateway -> VPC Link -> NLB -> auth
task -> Aurora as `auth_app`) actually works under live credentials.

```bash
API=$(cd infra/dev/api-gateway && terraform output -raw api_endpoint)

curl -sS -X POST "$API/v1/auth/sign-up" \
  -H 'content-type: application/json' \
  -d '{"email":"smoke-test@example.com","password":"correct-horse-battery-staple","name":"Smoke Test"}' \
  | jq .
```

Expected: HTTP 200 with a JSON body containing a JWT token. Verify the
row landed:

```bash
# Re-use the verify-grants pattern, but SELECT email, role from "user".
# Expected row: smoke-test@example.com / user
```

If the call returns 500 with a `permission denied for table "user"`
message in the auth task's CloudWatch logs, the grant set in step 6 is
incomplete. Re-run step 6 and recheck `information_schema.role_table_grants`.

### 10. Seed the first admin

Why: slice-1 admin assignment is a direct SQL update per
`services/auth/drizzle/0001_add_role.sql`'s comment. Promote Phil's
account so admin-tier endpoints become reachable.

```bash
# Sign up phil@lafayettelabs.com via the same API (or via the dashboard).
curl -sS -X POST "$API/v1/auth/sign-up" \
  -H 'content-type: application/json' \
  -d '{"email":"phil@lafayettelabs.com","password":"<a strong password Phil controls>","name":"Phillip LaFayette"}'

# Then promote via a one-off run-task (re-use the verify pattern):
PROMOTE_JS=$(cat <<'JS'
import postgres from "postgres";
const sql = postgres(process.env.DATABASE_URL, {max:1, prepare:false});
const rows = await sql`
  UPDATE "user" SET role = 'admin' WHERE email = 'phil@lafayettelabs.com'
  RETURNING email, role
`;
console.log(JSON.stringify({level:"info",msg:"admin_promoted",rows}));
await sql.end();
JS
)
# run-task against $MIGRATE_TD_ARN (master creds; UPDATE needs DML grant
# which auth_app also has, but using the master here keeps the privileged
# operation traceable in CloudTrail under the master role).
```

Verify with a SELECT through the same path:

```sql
SELECT email, role FROM "user" ORDER BY created_at;
```

Expected: `phil@lafayettelabs.com` with `role = admin`.

### 11. Cleanup / hygiene

Why: the migrate revision of the task definition and the
`database-url-migrate` secret are operational tools, not runtime
dependencies. Decide whether to keep or retire them.

```bash
# Deregister the one-off migrate task-def revision. The running service
# is on its own revision; deregistering the migrate revision does not
# affect it.
aws ecs deregister-task-definition --task-definition "$MIGRATE_TD_ARN"
```

**The trade-off on `panakoes-dev/database-url-migrate`.** Two postures:

- **Keep it (recommended for dev).** Future schema changes re-use the
  same secret. Steps 4 and 5 of this runbook collapse to two commands.
  Cost: one secret carries master credentials in Secrets Manager
  permanently. Mitigation: the secret's IAM grant lives only on the auth
  execution role and the admin operator role; rotation can be automated
  via a Secrets Manager rotation lambda alongside the cluster's master
  password rotation.
- **Delete it (recommended for prod).** Re-create it for each schema
  rollout from the master password secret, then delete after. Cost:
  every schema change re-runs step 3. Benefit: master credentials are not
  resident in a long-lived secret outside the cluster-master secret itself.

For dev, keep it. For prod, document deletion as part of every
schema-change PR's runbook.

```bash
# Only if deleting:
aws secretsmanager delete-secret \
  --secret-id panakoes-dev/database-url-migrate \
  --recovery-window-in-days 7
```

### 12. Routine migration (after first-deploy)

For subsequent schema changes against a cluster that has already gone
through first-deploy:

1. Push a new auth image to ECR.
2. `terraform apply` `infra/dev/ecs` with the new tag.
3. If `database-url-migrate` was kept (step 11 "keep"), run the wrapper.
   By default the wrapper picks the running service's task def (which
   has the runtime `auth_app` secret and no DDL privileges), so for
   routine migrations pass the migrate task-def revision via
   `--task-definition` (or the `TASK_DEFINITION` env var). The flag
   was added when `lifecycle.ignore_changes = [task_definition]` made
   service-discovery resolve to an inactive older revision after a
   fresh `terraform apply`:

   ```bash
   # Register (or look up) the migrate task-def revision per step 4
   # first, then:
   AWS_PROFILE=panakoes-admin ./scripts/run-auth-migration.sh \
     --task-definition "$MIGRATE_TD_ARN"
   ```
4. Force-redeploy if the new image has runtime changes
   (`aws ecs update-service --force-new-deployment`).

### 13. Subsequent migration deployments (post-cleanup)

If step 11 deleted `panakoes-dev/database-url-migrate` (the default
"clean up" path), the auth task definition only references
`panakoes-dev/database-url`, which holds the runtime `auth_app`
credentials and is missing the `ALTER TABLE` / `CREATE TABLE` grants
that DDL requires. Every subsequent migration therefore temporarily
swaps master credentials into `panakoes-dev/database-url`, runs the
migrator, and reverts. The swap is in-place on the runtime secret;
the secret ARN never changes, so no terraform apply is required.

**Preconditions:**

1. A new auth image is in ECR that contains the migration SQL file(s)
   under `services/auth/drizzle/` (i.e., the same image you intend to
   deploy as the runtime). The migrator reads files from the container
   filesystem; an older image cannot apply a newer migration. Verify
   the image tag matches the registered task definition revision:

   ```bash
   AWS_PROFILE=panakoes-admin aws ecs describe-task-definition \
     --task-definition panakoes-dev-auth \
     --query 'taskDefinition.{revision:revision,image:containerDefinitions[0].image}' \
     --output json
   ```

   If the latest registered revision predates the migration's merge
   commit, `terraform apply infra/dev/ecs` (with the new
   `TF_VAR_auth_image_tag`) first, then proceed.

2. The `panakoes-dev/postgres-auth-db-password` secret holds the
   current master password (created in step 1, never deleted).

**Procedure:**

```bash
export AWS_PROFILE=panakoes-admin
export AWS_REGION=us-east-1

# 1. Record the CURRENT (auth_app) version id so we can revert exactly.
PRIOR_VERSION_ID=$(aws secretsmanager list-secret-version-ids \
  --secret-id panakoes-dev/database-url \
  --query 'Versions[?contains(VersionStages, `AWSCURRENT`)].VersionId | [0]' \
  --output text)
echo "PRIOR_VERSION_ID=$PRIOR_VERSION_ID"

# 2. Compose master URL and put it as the new AWSCURRENT version.
#    The master password is read directly from Secrets Manager and never
#    echoed; the composed URL is piped into put-secret-value via env, not
#    a shell variable that would land in `set -x` output.
CLUSTER_HOST=panakoes-dev-auth-20260510055543895900000001.cluster-cm18asy2wvdl.us-east-1.rds.amazonaws.com
MASTER_PW=$(aws secretsmanager get-secret-value \
  --secret-id panakoes-dev/postgres-auth-db-password \
  --query SecretString --output text)
URL="postgres://panakoes_auth:${MASTER_PW}@${CLUSTER_HOST}:5432/panakoes_auth?sslmode=require"
aws secretsmanager put-secret-value \
  --secret-id panakoes-dev/database-url \
  --secret-string "$URL" >/dev/null
unset MASTER_PW URL

# 3. Look up the latest task definition revision (carries the new
#    migration SQL inside the image) and run the migrator against it.
LATEST_REV=$(aws ecs describe-task-definition \
  --task-definition panakoes-dev-auth \
  --query 'taskDefinition.revision' --output text)
./scripts/run-auth-migration.sh \
  --task-definition "panakoes-dev-auth:${LATEST_REV}"

# 4. ALWAYS revert. The runtime auth service polls the secret on each
#    DB call; leaving master creds in place would mean the running auth
#    pods are operating with DDL privileges, violating ADR-039's
#    split-credential model.
PRIOR_VAL=$(aws secretsmanager get-secret-value \
  --secret-id panakoes-dev/database-url \
  --version-id "$PRIOR_VERSION_ID" \
  --query SecretString --output text)
aws secretsmanager put-secret-value \
  --secret-id panakoes-dev/database-url \
  --secret-string "$PRIOR_VAL" >/dev/null
unset PRIOR_VAL
```

**Verification after revert:** the AWSCURRENT secret-string sha256 must
match the `--version-id $PRIOR_VERSION_ID` secret-string sha256:

```bash
aws secretsmanager get-secret-value \
  --secret-id panakoes-dev/database-url \
  --query SecretString --output text | sha256sum
aws secretsmanager get-secret-value \
  --secret-id panakoes-dev/database-url \
  --version-id "$PRIOR_VERSION_ID" \
  --query SecretString --output text | sha256sum
```

**Rollback (if the migrator fails mid-run):** run only the step-4
revert block above. The migrator runs each SQL file inside its own
transaction, so a failed file's DDL is rolled back at the database
level; the bookkeeping `__migrations` row is only inserted on commit.
No manual cleanup of partial schema state is required.

**Why this lane exists at all** (interview talk-track): we deliberately
keep the runtime secret ARN stable across migrations so the auth task
definition does not need a fresh `terraform apply` on every schema
change. The trade-off is a short window (typically <60 seconds) where
`panakoes-dev/database-url` holds master credentials. The runtime
auth service caches its DB connection from process startup; the
in-flight runtime pods do not pick up the swapped credentials, and the
new one-off migrator task is the only consumer reading the AWSCURRENT
version during the window. We accept the window because the
alternatives (a permanent second secret, or rolling the task
definition on every migration) each carry larger ongoing cost than the
~60-second exposure.

## Verification

After step 9 + step 10 complete cleanly, all of the following must hold:

- `aws ecs describe-services --cluster panakoes-dev --services panakoes-dev-auth`
  shows `runningCount == desiredCount` and a recent deployment id.
- `POST /v1/auth/sign-up` against the API Gateway endpoint returns 200 with
  a JWT.
- A `SELECT email, role FROM "user"` (via one-off run-task) shows both the
  smoke-test row and Phil's admin row.
- `SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants
   WHERE grantee = 'auth_app'` returns exactly 16 rows (4 tables x 4 privileges).
- `SELECT 1 FROM "__migrations" WHERE filename = '0001_add_role.sql'` returns
  one row (the migrator recorded both files).
- The auth CloudWatch log group contains no `permission denied` errors
  since the force-redeploy.

## Rollback

The procedure is reversible up to and including the smoke test. After
real user data lands in the cluster, rollback is partial (you cannot
unsign-up users without dropping data).

**To roll back step 6 (drop the `auth_app` role + revoke grants):**

```sql
REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE "user", "session", "account", "verification" FROM auth_app;
REVOKE USAGE ON SCHEMA public FROM auth_app;
DROP ROLE auth_app;
```

Run via a one-off task using the migrate task-def (master creds). Then
overwrite `panakoes-dev/database-url` back to a placeholder via
`put-secret-value`. The service will fail on its next deploy with an
auth error against Postgres, which is the intended "rollback complete"
signal.

**To roll back step 5 (the migrations themselves):**

Drizzle does not generate down-migrations by default and the runtime
runner does not support reverse application. For first-deploy rollback,
the safest path is to take a fresh snapshot of the cluster, then drop the
four Better-Auth tables plus the `__migrations` table via the master role.
This is destructive; only do it before any real users exist.

```sql
DROP TABLE IF EXISTS "session", "account", "verification", "user", "__migrations" CASCADE;
```

**To roll back step 2 (the new image revision):**

```bash
TF_VAR_auth_image_tag=<previous-tag> terraform apply
aws ecs update-service --cluster panakoes-dev --service panakoes-dev-auth --force-new-deployment
```

Pair with rolling back step 5 if the previous image is incompatible with
the schema that landed.

## What this validates for interviews

This runbook is the operational proof that the design in ADR-039 actually
ships. Three patterns are explicit and defensible:

- **Principle of least privilege at the database tier.** Two roles, two
  secrets, scoped grants. A compromise of the running service cannot
  alter schema or escalate roles. The grant set is the minimum Better-Auth
  needs, not the convenient maximum.
- **DDL separated from DML by credential, not by deploy timing.**
  Migrations run as the master user via a one-off operator-invoked task.
  The running service never holds DDL privileges, so no in-band schema
  change is possible from a compromised request handler. The migration
  pipeline is a separate IAM + Secrets surface from the request pipeline.
- **One-off operational tasks run on the same VPC / SG / IAM as production
  traffic.** No bastion host, no operator SSH, no second long-lived IAM
  role. The migrator reuses the auth task definition (with one secret
  override), runs for ~30 seconds inside the same private subnets, and
  exits. The attack surface for "operator runs a migration" equals the
  attack surface for "service serves a request."

## References

- [ADR-039: Auth DB split-credential model and operator-invoked migration runner](../adr/ADR-039-auth-db-application-role-and-migration-runner.md)
- [ADR-036: Aurora Serverless v2 scale-to-zero](../adr/ADR-036-aurora-serverless-v2-scale-to-zero.md)
- `services/auth/src/migrate.ts`, `services/auth/drizzle/0000_initial.sql`,
  `services/auth/drizzle/0001_add_role.sql`
- `scripts/run-auth-migration.sh` (the wrapper; routine path)
- `infra/dev/auth-db/README.md` (cluster module)
- `infra/dev/ecs/main.tf` (auth task definition; `local.database_url_secret_arn`)
- `infra/dev/secrets/` (Secrets Manager provisioning)
- `infra/dev/iam/` (auth execution role's secret-ARN allowlist)
- AWS docs: `aws ecs run-task --overrides` reference (containerOverrides shape)
- PostgreSQL: `GRANT`, `REVOKE`, `CREATE ROLE`

# ADR-039: Auth DB split-credential model and operator-invoked migration runner

## Status

Accepted (2026-05-11).

## Context

The auth microservice (`services/auth/`) is a TypeScript Hono application that
uses Better-Auth on top of a dedicated Aurora Serverless v2 Postgres cluster
(`panakoes-dev-auth-20260510055543895900000001`, see `infra/dev/auth-db/`).
Better-Auth manages four tables: `user`, `session`, `account`, and
`verification` (schema in `services/auth/src/db/schema.ts`; SQL in
`services/auth/drizzle/0000_initial.sql` and `0001_add_role.sql`).

The auth-db Terraform module provisions the cluster with one master user
(`panakoes_auth`, owner of the `panakoes_auth` database). That user has full
DDL + DML privileges. Two design questions surface as soon as we try to ship
the service against the live cluster:

1. **Credential scope.** Does the running ECS service talk to Postgres as the
   master user, or as a less-privileged role? The auth service's steady-state
   workload is INSERT / SELECT / UPDATE / DELETE on four well-known tables.
   It never needs to CREATE TABLE or DROP TABLE at runtime. Running as the
   master user means any code-execution or SQL-injection foothold in the auth
   container is a full take-over of the auth database (drop tables, exfil
   every row, ALTER ROLE another user). Running as a least-privileged role
   means the same foothold can read and write Better-Auth rows (still bad,
   but bounded; no schema mutation, no privilege escalation).

2. **Migration invocation.** When does `drizzle/*.sql` actually run against
   the live cluster? Options span from "auto-apply at container startup" to
   "operator runs it explicitly." Auto-apply on boot is convenient for a
   single-task service but races on rolling deploys (N+1 tasks all racing the
   same DDL), couples schema migration to image rollout (impossible to roll
   back the image without also rolling back schema), and removes the
   operator's deliberate pause before a destructive change.

These two questions are coupled: DDL needs a privileged credential, DML does
not, and the cleanest expression of that split is two distinct database roles
behind two distinct Secrets Manager entries.

## Decision

### A. Split the Postgres credentials in two

The auth service uses two database roles in the dev cluster, each backed by a
distinct Secrets Manager secret:

| Role | Privileges | Secret | Use |
|---|---|---|---|
| `panakoes_auth` (master, Aurora-provisioned) | OWNER of database `panakoes_auth`, full DDL + DML | `panakoes-dev/database-url-migrate` (new in this ADR) | One-off migration runs only |
| `auth_app` (application role, created during first deploy) | LOGIN; `USAGE` on schema `public`; `SELECT, INSERT, UPDATE, DELETE` on `"user"`, `"session"`, `"account"`, `"verification"` | `panakoes-dev/database-url` (already wired into the auth ECS task definition) | Running service |

The runtime task definition (`infra/dev/ecs/main.tf`) already injects
`DATABASE_URL` from `panakoes-dev/database-url` (`local.database_url_secret_arn`
in the module). This ADR retains that wiring; the only change is that the
secret's value is rewritten to encode the `auth_app` credentials rather than
the master credentials during the first deploy.

`auth_app` carries the minimum grant set Better-Auth needs:

- `USAGE` on schema `public` so the role can resolve the four table names.
- `SELECT, INSERT, UPDATE, DELETE` on `"user"`, `"session"`, `"account"`,
  `"verification"`. No `TRUNCATE`, no `REFERENCES`, no `TRIGGER`.
- No grants on the `__migrations` bookkeeping table (the migration runner
  owns that table; runtime code has no reason to touch it).
- No `CREATE` on schema `public`, so a compromised auth container cannot
  drop or create tables.
- No role-management privileges (`CREATEROLE`, `CREATEDB`, `SUPERUSER`,
  `INHERIT FROM` master).

The `__migrations` table created by the runtime runner (see
`services/auth/src/migrate.ts`) is owned by `panakoes_auth` by default; that
ownership is correct, no operator action needed.

### B. Migrations run via one-off `aws ecs run-task`, not at service startup

`services/auth/src/migrate.ts` is the runtime migration runner that ships in
the auth Docker image (`dist/migrate.js`). The operator-facing path is
`scripts/run-auth-migration.sh`, which:

1. Describes the running auth ECS service to discover its current task
   definition revision, subnets, security groups, and CloudWatch log config.
2. Calls `aws ecs run-task` against the same task definition with a
   `containerOverrides.command` of `["node", "dist/migrate.js"]`.
3. Polls until the task reaches `STOPPED`, then prints the container's
   CloudWatch log stream and exits with the container's exit code.

The auth service's normal startup path never calls the migrator. The
container entrypoint runs the HTTP server only.

## Consequences

**Positive.**

- **Principle of least privilege at the database tier.** A compromise of the
  auth ECS task surface (Hono router, Better-Auth, jose, otpauth, postgres-js)
  cannot drop tables, create roles, or escalate inside the database. The
  blast radius is bounded to "read + write Better-Auth rows," which is
  already the worst case for any auth bug; the credential boundary just
  prevents that bug from becoming a database take-over.
- **Schema changes are deliberate.** Rolling deploys (N tasks racing) cannot
  race a DDL statement, because new tasks never touch DDL. Schema rollout
  and image rollout are independently revertable: `terraform apply` a prior
  `auth_image_tag` to roll back code without touching tables.
- **One-off task reuses the production network + IAM surface.** The migrator
  runs as the same task definition (same subnets, same security groups,
  same execution role, same task role, same VPC interface endpoints) as the
  running service. There is no bastion host, no operator laptop reaching
  through a tunnel, no second IAM role to audit. The attack surface for
  "operator runs a migration" is exactly the attack surface for "service
  serves a request."
- **The `__migrations` table makes runs idempotent.** Re-invoking the
  wrapper is safe; already-applied files are skipped on hash match and the
  runner refuses to continue on hash mismatch (catches edits to past
  migrations).
- **The DDL credential is not online during normal operation.** The
  `panakoes-dev/database-url-migrate` secret is read only during a one-off
  task run. The runtime task never sees it. A compromise of the runtime
  task's IAM cannot fetch it because the execution role's
  `secretsmanager:GetSecretValue` grant scopes to a different secret ARN.
  (Today the grant is broad enough to read both; tightening to a specific
  ARN is a follow-up captured in `infra/dev/iam/`.)

**Negative.**

- **One more secret to operate.** Two `database-url*` secrets now exist in
  `panakoes-dev/`. The runbook (`docs/runbooks/auth-db-first-deploy.md`)
  documents the cleanup posture (delete vs retain the migrate secret after
  first deploy; trade-off explicitly noted).
- **First-deploy is multi-step.** Creating the `auth_app` role + grants
  requires a one-off psql run against the cluster. That is one more thing
  for the runbook than "deploy the image and walk away." We accept this
  cost; the runbook is concrete and the steps are reversible.
- **`pg_hba.conf` cannot enforce the boundary at Aurora.** Aurora does not
  expose `pg_hba.conf`, so the credential split is the only enforcement
  layer. A leaked migrate-secret value would still authenticate against
  the cluster from anywhere inside the VPC (the cluster security group's
  ingress is VPC-CIDR-wide today). KMS-at-rest + IAM scope on the secret +
  short-lived rotation is the defense-in-depth answer; the auth-db README
  documents the follow-up tightening pass to SG-to-SG ingress.
- **Schema changes require an operator with `panakoes-admin` AWS profile.**
  Service teams cannot ship a schema change unattended; an operator runs
  the wrapper script. For a single-developer project this is fine; at
  team size the workflow needs an automation seam (a tightly scoped
  GitHub Actions job that assumes a migrate-only role, scoped to one
  migrate-secret read + one `ecs:RunTask` call against the auth cluster).
  Out of scope for this ADR.

## Alternatives considered

**Auto-apply migrations on auth service startup.** Rejected. A rolling
deploy of N+1 tasks would race the same DDL. Better-Auth's tables happen
to be `CREATE TABLE IF NOT EXISTS` shaped so the race is benign for the
initial migration, but `0001_add_role.sql` uses `ADD CONSTRAINT` which is
not idempotent across racing transactions. More fundamentally, schema
rollout shares fate with image rollout; rolling back a bad image after the
migration has applied means rolling back to a code base that may not
understand the new schema. Operator-invoked separation makes the two
revertable independently.

**Lambda-driven migrator triggered by a CodeBuild step or a custom resource.**
Rejected. We would carry a second image (Lambda layer or container), a
second IAM role, and a second deploy path for the same code that already
ships in the auth container. The "one-off ECS task reuses the auth task
definition" pattern reuses everything already audited for the runtime
service: the same image, the same execution role, the same network. Adding
a Lambda is more surface, not less.

**Bastion EC2 + `psql` from an operator laptop.** Rejected. A bastion is
a long-lived host with SSH ingress, a long-lived IAM role, a fleet of
operator public keys, AMI patch hygiene, and a separate set of detective
controls. The one-off ECS task pattern carries none of that surface; the
task exists for ~30 seconds and inherits IAM from the task role that
already exists. We pay zero standing cost (no idle EC2) and zero standing
attack surface.

**Dedicated migrator container image (separate from the auth image).**
Considered. A `panakoes-dev-auth-migrator` image owning only the migration
code keeps the runtime auth image smaller (no `dist/migrate.js`). Rejected
because (a) `dist/migrate.js` is ~3 KB after tsc, the size argument is
noise, and (b) two images means two CI build paths, two ECR repos, two
image-rotation cadences, and a real risk of the migrator image drifting
behind the schema the runtime image expects. One image, same artifact, is
the simpler decomposition.

**Single master-user setup (no `auth_app` role).** Rejected. This is the
"everything as panakoes_auth" path. It works, it is simpler operationally,
and it is the wrong security posture for a public-internet authentication
service. Every interview pen-test on this design (and there will be one,
that is the point of the portfolio piece) will ask "why does your auth
service run as a database owner." The honest answer is "it does not, see
ADR-039." Spending the runbook complexity buys defensible least-privilege
posture across every downstream conversation.

## References

- `services/auth/src/migrate.ts` (the runtime migration runner)
- `services/auth/src/db/schema.ts` (Better-Auth tables: `user`, `session`,
  `account`, `verification`)
- `services/auth/drizzle/0000_initial.sql`,
  `services/auth/drizzle/0001_add_role.sql` (the two migrations)
- `services/auth/README.md` section "Database migrations"
- `scripts/run-auth-migration.sh` (the `aws ecs run-task` wrapper)
- `infra/dev/auth-db/README.md` (Aurora cluster module + post-apply notes)
- `infra/dev/ecs/main.tf` (auth task definition; `DATABASE_URL` injected
  from `panakoes-dev/database-url`)
- `docs/runbooks/auth-db-first-deploy.md` (operator procedure that applies
  this ADR)
- ADR-036 (Aurora Serverless v2 scale-to-zero context for the cluster)
- ADR-022 (JWT signing posture; the auth service is the consumer of this
  database)
- PostgreSQL documentation: `GRANT`, `REVOKE`, role attributes

# Seed an admin user

## Purpose

Promote an existing Panakoes user account to `role=admin` so the user can
access the admin SPA (`/dashboard`, `/lifecycle`, etc.). The admin SPA
will not render past the layout guard for non-admin users; today the only
way to mint the first admin (or any future admin) is to flip the `role`
column on the `user` table in Aurora.

This runbook wraps that flip in a one-liner:

```bash
EMAIL=foo@example.com AWS_PROFILE=panakoes-admin make seed-admin
```

The underlying script (`services/auth/scripts/seed-admin.sh`) exec's into
a running `panakoes-dev-auth` ECS task and runs an idempotent `UPDATE`.

The auth runtime image does NOT ship `psql` (verified 2026-06-04: an
in-container `psql` invocation returns `sh: 1: psql: not found`). The
script therefore runs a small `node` one-shot instead: the image ships
`node` plus the `postgres` npm package at `/app/node_modules/postgres`
and exposes `DATABASE_URL` in the task environment. The node one-shot is
the canonical execution path; do NOT re-add `postgresql-client` to the
image just to support this script.

## When to use this runbook

- A new operator (you on a fresh laptop, a teammate, a new environment)
  needs admin access and there is no admin UI surface for promoting
  users yet.
- A user has already signed up through the normal sign-up flow but their
  row defaults to `role='user'`.
- You need to bootstrap the first admin on a brand-new dev environment.

This is NOT for creating a user. The user must have already signed up
through `/auth/sign-up` (or the SPA's sign-in flow with sign-up enabled).

## Prerequisites

| Tool / state | Verification |
|---|---|
| `aws` CLI | `aws --version` |
| `jq` | `jq --version` |
| Session Manager plugin | `session-manager-plugin --version` (required by `aws ecs execute-command`) |
| `AWS_PROFILE` env var set | `echo $AWS_PROFILE` returns `panakoes-admin` (or equivalent profile with ECS exec + Secrets Manager read) |
| `panakoes-dev-auth` ECS service running | `aws ecs describe-services --cluster panakoes-dev --services panakoes-dev-auth --query 'services[0].runningCount'` returns >= 1 |
| Target user has already signed up | They show up in the `user` table |
| `enable_execute_command = true` on the auth service | Set in `infra/dev/ecs/auth.tf` |

If `AWS_PROFILE` is unset the script exits 2 with a clear message before
making any AWS API call. Same for missing `EMAIL`.

## Steps

1. **Set your AWS profile** (one-time per shell):

   ```bash
   export AWS_PROFILE=panakoes-admin
   ```

2. **Run the make target** with the user's email:

   ```bash
   make seed-admin EMAIL=phil@lafayettelabs.com
   ```

3. **Read the output.** Three terminal cases:

   - `promoted <email> to role=admin` (exit 0): the user is now an admin.
   - `user <email> already has role=admin, nothing to do` (exit 0): the
     user was already admin; the script is idempotent.
   - `user <email> not found in the user table; have they signed up yet?`
     (exit 1): the email has no row in the `user` table. Have them sign
     up first, then re-run.

4. **Verify in the SPA.** Sign in as the promoted user at
   `https://dmaopcm3hnxog.cloudfront.net/` and confirm the dashboard
   renders (admin routes return 200 instead of 403).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `AWS_PROFILE is required` (exit 2) | The shell has no `AWS_PROFILE` set | `export AWS_PROFILE=panakoes-admin` |
| `no RUNNING task found in panakoes-dev/panakoes-dev-auth` (exit 3) | The auth ECS service has zero RUNNING tasks; usually a failed deploy | Check `aws ecs describe-services` events, deploy a healthy revision first |
| `ExecuteCommand` API call fails with `InvalidParameterException: ... enable execute command` | `enable_execute_command` is false on the service | Update `infra/dev/ecs/auth.tf`, `terraform apply` |
| `An error occurred (AccessDeniedException)` from ECS exec | The IAM role you're using lacks `ssmmessages:*` or `ecs:ExecuteCommand` | Use `panakoes-admin` profile, or add the permissions to your role |
| `psql: command not found` inside container | An OLD copy of the script (or a manual psql exec) is being used | The current script no longer calls psql; it uses a `node` one-shot against `/app/node_modules/postgres`. Pull the latest `services/auth/scripts/seed-admin.sh`. Do NOT add `postgresql-client` to the image. |
| `could not determine outcome from the node one-shot output` | The node one-shot printed neither a sentinel nor a clean `ERR:` line | Read the raw exec output the script echoes; common causes are `/app/node_modules/postgres` missing from the image or `DATABASE_URL` unset in the task |
| Output prints `SEED_ADMIN_NOT_FOUND` but you can see the user in the SPA | Email comparison is case-insensitive, but if the user is in a different tenant / dataset, double-check the connection target | Confirm `$DATABASE_URL` in the task points at the expected cluster |

## Cleanup

None. The `UPDATE` is a single-row change to the application database
and the ECS exec session ends as soon as the node one-shot exits.

To demote an admin back to a regular user, run the same `UPDATE`
manually (no helper script today):

```sql
UPDATE "user" SET role = 'user' WHERE lower(email) = lower('foo@example.com');
```

## Related

- `services/auth/scripts/seed-admin.sh`: the script the make target wraps.
- `scripts/run-auth-migration.sh`: the pattern this script borrows from
  (operator-invoked one-off task against the auth service).
- `docs/runbooks/auth-db-first-deploy.md`: bootstrap the auth DB before
  the first user can sign up.

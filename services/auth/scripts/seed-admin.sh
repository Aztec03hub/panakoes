#!/usr/bin/env bash
# seed-admin.sh: promote a user to role=admin against the auth service's
# Aurora database via an `aws ecs execute-command` session into a running
# panakoes-dev-auth task.
#
# Why a script: today the only way to mint an admin is to `UPDATE "user"
# SET role='admin' WHERE email='...'` directly against Aurora through a
# bastion or a hand-rolled ecs exec. This is repeatable, idempotent, and
# exits 0 on the "already admin" case so it slots cleanly into bootstrap
# automation.
#
# Required tooling: aws, jq, the SSM Session Manager plugin (for ECS exec).
# Required env:
#   EMAIL                target user's email address (case-insensitive
#                         compare against the `user.email` column)
#   AWS_PROFILE          named profile with ECS exec + Secrets Manager
#                         read on the auth task; exits 2 if unset
# Optional env:
#   AWS_REGION           default us-east-1
#   CLUSTER              default panakoes-dev
#   SERVICE              default panakoes-dev-auth
#
# Exit codes:
#   0  user is admin (either just promoted, or already admin)
#   1  user not found / unexpected DB error
#   2  bad usage / required env missing / dependency missing
#   3  could not reach a running auth task (ECS exec setup failure)

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: EMAIL=foo@example.com AWS_PROFILE=panakoes-admin services/auth/scripts/seed-admin.sh

Promotes a user to role=admin by exec'ing into a running auth ECS task and
running `psql $DATABASE_URL`. Idempotent: prints "already admin, nothing
to do" and exits 0 when the user already has role=admin.

Required env:
  EMAIL                   email of the user to promote
  AWS_PROFILE             named profile with ECS exec perms

Optional env:
  AWS_REGION              default us-east-1
  CLUSTER                 default panakoes-dev
  SERVICE                 default panakoes-dev-auth

Exit codes:
  0  user is admin (promoted or already-admin)
  1  user not found in the database
  2  bad usage / missing dependency / missing env
  3  could not reach a running auth task

Example:
  EMAIL=phil@lafayettelabs.com AWS_PROFILE=panakoes-admin \
    services/auth/scripts/seed-admin.sh
USAGE
}

# printf '%s\n' avoids treating messages starting with `--` as flags and
# avoids consuming literal % as a format spec; matches the convention in
# scripts/run-auth-migration.sh.
log() { printf '%s\n' "$*"; }
err() { printf '%s\n' "$*" >&2; }

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "missing dependency: $1"
    exit 2
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    *) err "unknown argument: $1"; usage >&2; exit 2 ;;
  esac
done

need aws
need jq

if [[ -z "${EMAIL:-}" ]]; then
  err "EMAIL is required (e.g. EMAIL=foo@example.com)"
  exit 2
fi

if [[ -z "${AWS_PROFILE:-}" ]]; then
  err "AWS_PROFILE is required (e.g. export AWS_PROFILE=panakoes-admin)"
  exit 2
fi

AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER="${CLUSTER:-panakoes-dev}"
SERVICE="${SERVICE:-panakoes-dev-auth}"

export AWS_PROFILE AWS_REGION

aws_q() {
  aws --no-cli-pager "$@"
}

# Reject obviously invalid emails early. Cheap guard against a typo
# triggering a multi-second ECS exec round-trip just to find out.
if ! [[ "$EMAIL" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]]; then
  err "EMAIL does not look like a valid address: $EMAIL"
  exit 2
fi

log "looking up a running task in $CLUSTER/$SERVICE..."

task_arn=$(aws_q ecs list-tasks \
  --cluster "$CLUSTER" \
  --service-name "$SERVICE" \
  --desired-status RUNNING \
  --query 'taskArns[0]' \
  --output text)

if [[ -z "$task_arn" || "$task_arn" == "None" ]]; then
  err "no RUNNING task found in $CLUSTER/$SERVICE; deploy the auth service first"
  exit 3
fi

task_id="${task_arn##*/}"
log "using task $task_id"

# Build the psql snippet. The auth service's runtime image ships psql
# (or alternatively a Node-based one-shot via better-auth/drizzle; we
# stick with psql for parity with run-auth-migration.sh's expectations).
# The SQL is single-quote-safe because EMAIL is bash-quoted; we further
# escape any embedded single quotes by doubling them per Postgres rules.
escaped_email=${EMAIL//\'/\'\'}

# This SQL does three things atomically:
#   1. Looks up the current role for the target email.
#   2. If role is already admin, prints SEED_ADMIN_ALREADY for the wrapper.
#   3. Else if the user exists, updates and prints SEED_ADMIN_PROMOTED.
#   4. Else prints SEED_ADMIN_NOT_FOUND.
# Sentinels keep this script robust to psql output formatting changes.
sql=$(cat <<SQL
DO \$\$
DECLARE
  current_role text;
BEGIN
  SELECT role INTO current_role
  FROM "user"
  WHERE lower(email) = lower('${escaped_email}')
  LIMIT 1;

  IF current_role IS NULL THEN
    RAISE NOTICE 'SEED_ADMIN_NOT_FOUND';
  ELSIF current_role = 'admin' THEN
    RAISE NOTICE 'SEED_ADMIN_ALREADY';
  ELSE
    UPDATE "user"
       SET role = 'admin'
     WHERE lower(email) = lower('${escaped_email}');
    RAISE NOTICE 'SEED_ADMIN_PROMOTED';
  END IF;
END
\$\$;
SQL
)

# Wrap the SQL in a shell command that the container will run. Use
# psql's -v ON_ERROR_STOP=1 so any DB error returns non-zero from psql,
# which then surfaces through ecs execute-command.
remote_cmd="psql \"\$DATABASE_URL\" -v ON_ERROR_STOP=1 -X -A -t <<'PSQL_EOF'
${sql}
PSQL_EOF"

log "exec'ing into task $task_id to run psql..."

# `aws ecs execute-command` is interactive by default. Use --command with
# a sh -c that runs the heredoc; capture stdout/stderr for parsing.
# stderr is where NOTICE messages land in psql; merge with stdout.
exec_out=$(aws_q ecs execute-command \
  --cluster "$CLUSTER" \
  --task "$task_arn" \
  --interactive \
  --command "sh -c '${remote_cmd}'" 2>&1 || true)

# Surface the raw exec output to the user for debuggability; the
# sentinels we look for are nested inside it.
log "--- begin exec output ---"
printf '%s\n' "$exec_out"
log "--- end exec output ---"

if printf '%s' "$exec_out" | grep -q 'SEED_ADMIN_ALREADY'; then
  log "user ${EMAIL} already has role=admin, nothing to do"
  exit 0
fi

if printf '%s' "$exec_out" | grep -q 'SEED_ADMIN_PROMOTED'; then
  log "promoted ${EMAIL} to role=admin"
  exit 0
fi

if printf '%s' "$exec_out" | grep -q 'SEED_ADMIN_NOT_FOUND'; then
  err "user ${EMAIL} not found in the user table; have they signed up yet?"
  exit 1
fi

err "could not determine outcome from psql output (see above); inspect manually"
exit 1

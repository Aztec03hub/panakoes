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
# How the UPDATE runs: the auth runtime image does NOT ship `psql`
# (verified 2026-06-04: "sh: 1: psql: not found"). Instead the image ships
# node plus the `postgres` npm package at /app/node_modules/postgres, with
# DATABASE_URL in the task environment. This script exec's a node one-shot
# that opens a single connection, applies the idempotent three-outcome
# logic, and prints one of the SEED_ADMIN_* sentinels for the wrapper to
# parse. No postgresql-client is required in the runtime image.
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
running a node one-shot against $DATABASE_URL (the runtime image ships node
plus the `postgres` npm package, not psql). Idempotent: prints "already
admin, nothing to do" and exits 0 when the user already has role=admin.

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

# Build the node one-shot. The auth runtime image ships node plus the
# `postgres` npm package at /app/node_modules/postgres and DATABASE_URL in
# the task environment; it does NOT ship psql. The email is embedded into a
# JS template literal, so we must escape the characters that are special to
# a template literal: backslash, backtick, and the `${` interpolation
# opener. The earlier regex guard already rejects whitespace and stray `@`,
# so the remaining risk surface is exactly those three. We escape them in a
# fixed order (backslash first, so we do not double-escape the escapes we
# add for backtick / dollar).
js_email=$EMAIL
js_email=${js_email//\\/\\\\}   # \  -> \\
js_email=${js_email//\`/\\\`}   # `  -> \`
js_email=${js_email//\$/\\\$}   # $  -> \$  (neutralises ${...} interpolation)

# The node snippet does three things over a single connection:
#   1. Looks up the current role for the target email (case-insensitive).
#   2. If role is already admin, prints SEED_ADMIN_ALREADY for the wrapper.
#   3. Else if the user exists, updates and prints SEED_ADMIN_PROMOTED.
#   4. Else prints SEED_ADMIN_NOT_FOUND.
# Sentinels keep this script robust to driver output formatting changes.
# Parameterised tagged-template bindings (\${...}) are used so the email and
# the literal "admin" value never participate in SQL parsing; the
# template-literal escaping above only protects the surrounding JS string.
node_snippet=$(cat <<NODE
const postgres = require("/app/node_modules/postgres");
const sql = postgres(process.env.DATABASE_URL, { max: 1 });
(async () => {
  const email = \`${js_email}\`;
  const adminRole = "admin";
  const rows = await sql\`select role from "user" where lower(email) = lower(\${email}) limit 1\`;
  if (rows.length === 0) {
    console.log("SEED_ADMIN_NOT_FOUND");
  } else if (rows[0].role === adminRole) {
    console.log("SEED_ADMIN_ALREADY");
  } else {
    await sql\`update "user" set role = \${adminRole} where lower(email) = lower(\${email})\`;
    console.log("SEED_ADMIN_PROMOTED");
  }
  await sql.end();
})().catch((e) => { console.error("ERR:" + e.message); process.exit(1); });
NODE
)

# Deliver the program to the container via base64.
#
# The delivery path is brutal on quoting: aws ecs execute-command takes the
# whole command as one `--command "sh -c '<remote_cmd>'"` argv string, so
# the SSM agent runs `sh -c '<remote_cmd>'`. The program body legitimately
# contains backticks (JS template literals + the `postgres` tagged-template),
# `${...}` interpolations, double quotes, and (in pathological emails) could
# contain anything. Trying to keep that intact through an outer single-quote
# wrapper plus an inner heredoc is fragile (a single quote, an unbalanced
# backtick, or a `$(` in the body breaks a layer).
#
# base64 sidesteps all of it: the encoded form is only [A-Za-z0-9+/=], none
# of which is special to any shell, so it survives every quoting layer
# verbatim. The container decodes it and pipes it to node on stdin. node
# reads its script from stdin when given no file/`-e` argument, and exits
# non-zero on the error path (process.exit(1)), surfacing through ecs
# execute-command. `base64 -w0` keeps the payload on a single line; the
# container side uses `base64 -d` (coreutils, present in the node:slim/
# debian base the auth image is built on).
b64=$(printf '%s' "$node_snippet" | base64 -w0)
remote_cmd="echo ${b64} | base64 -d | node"

log "exec'ing into task $task_id to run the node one-shot..."

# `aws ecs execute-command` is interactive by default. Use --command with
# a sh -c that decodes the base64 program and pipes it to node; capture
# stdout/stderr for parsing. The sentinels print to stdout; the ERR: line
# (if any) prints to stderr; we merge both so grep below sees everything.
exec_out=$(aws_q ecs execute-command \
  --cluster "$CLUSTER" \
  --task "$task_arn" \
  --container "${CONTAINER:-auth}" \
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

err "could not determine outcome from the node one-shot output (see above); inspect manually"
exit 1

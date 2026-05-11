#!/usr/bin/env bash
# run-auth-migration.sh: apply auth-service DB migrations via a one-off
# ECS run-task against the dev (or prod) cluster.
#
# The auth runtime image ships `dist/migrate.js` (see services/auth/src/migrate.ts).
# Rather than baking a sidecar or auto-running on startup, this script
# launches a single Fargate task that reuses the auth task definition,
# overrides the entrypoint to `node dist/migrate.js`, and tails the
# task's CloudWatch logs until the container exits. Operator-invoked so
# schema changes never race with a rolling deploy.
#
# Required tooling: aws, jq.
# Required env:
#   AWS_PROFILE          (no default)
# Optional env:
#   AWS_REGION           default us-east-1
#   CLUSTER              default panakoes-dev
#   SERVICE              default panakoes-dev-auth
#   POLL_INTERVAL_S      default 5
#   STARTUP_TIMEOUT_S    default 300
#
# Exit codes:
#   0  migration container exited 0
#   1  migration container exited non-zero (exit code surfaced verbatim)
#   2  bad usage / required env missing / dependency missing
#   3  task failed to start (capacity, networking, image pull, etc.)
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER="${CLUSTER:-panakoes-dev}"
SERVICE="${SERVICE:-panakoes-dev-auth}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-5}"
STARTUP_TIMEOUT_S="${STARTUP_TIMEOUT_S:-300}"

err() { printf '%s\n' "$*" >&2; }
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "missing dependency: $1"
    exit 2
  fi
}

need aws
need jq

if [[ -z "${AWS_PROFILE:-}" ]]; then
  err "AWS_PROFILE is required (set it in your shell, e.g. panakoes-admin)"
  exit 2
fi

export AWS_PROFILE AWS_REGION

aws_q() {
  # Wraps the aws CLI with --no-cli-pager so output is never paged when
  # this script runs unattended (CI, cron).
  aws --no-cli-pager "$@"
}

printf 'discovering network config from service %s/%s...\n' "$CLUSTER" "$SERVICE"

svc_json=$(aws_q ecs describe-services \
  --cluster "$CLUSTER" \
  --services "$SERVICE" \
  --query 'services[0]' \
  --output json)

if [[ -z "$svc_json" || "$svc_json" == "null" ]]; then
  err "could not describe service $SERVICE in cluster $CLUSTER"
  exit 3
fi

task_def=$(printf '%s' "$svc_json" | jq -r '.taskDefinition')
subnets=$(printf '%s' "$svc_json" | jq -r '.networkConfiguration.awsvpcConfiguration.subnets | join(",")')
security_groups=$(printf '%s' "$svc_json" | jq -r '.networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')
assign_public=$(printf '%s' "$svc_json" | jq -r '.networkConfiguration.awsvpcConfiguration.assignPublicIp // "DISABLED"')
launch_type=$(printf '%s' "$svc_json" | jq -r '.launchType // "FARGATE"')
platform_version=$(printf '%s' "$svc_json" | jq -r '.platformVersion // "LATEST"')

if [[ -z "$task_def" || "$task_def" == "null" || -z "$subnets" || -z "$security_groups" ]]; then
  err "service describe did not return required network fields"
  exit 3
fi

# Inspect the task definition once to find the container name (avoid
# guessing) and capture the CloudWatch log config so we can tail later.
td_json=$(aws_q ecs describe-task-definition \
  --task-definition "$task_def" \
  --query 'taskDefinition' \
  --output json)

container_name=$(printf '%s' "$td_json" | jq -r '.containerDefinitions[0].name')
log_group=$(printf '%s' "$td_json" | jq -r '.containerDefinitions[0].logConfiguration.options."awslogs-group" // empty')
log_prefix=$(printf '%s' "$td_json" | jq -r '.containerDefinitions[0].logConfiguration.options."awslogs-stream-prefix" // empty')

printf 'using task def: %s\n' "$task_def"
printf 'container: %s, log group: %s, prefix: %s\n' "$container_name" "$log_group" "$log_prefix"

overrides=$(jq -nc \
  --arg name "$container_name" \
  '{containerOverrides: [{name: $name, command: ["node", "dist/migrate.js"]}]}')

network_config=$(jq -nc \
  --arg subnets "$subnets" \
  --arg sgs "$security_groups" \
  --arg pub "$assign_public" \
  '{awsvpcConfiguration: {subnets: ($subnets | split(",")), securityGroups: ($sgs | split(",")), assignPublicIp: $pub}}')

printf 'launching one-off migration task...\n'
run_json=$(aws_q ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$task_def" \
  --launch-type "$launch_type" \
  --platform-version "$platform_version" \
  --count 1 \
  --started-by "run-auth-migration-$(date -u +%Y%m%dT%H%M%SZ)" \
  --network-configuration "$network_config" \
  --overrides "$overrides" \
  --output json)

task_arn=$(printf '%s' "$run_json" | jq -r '.tasks[0].taskArn // empty')
failures=$(printf '%s' "$run_json" | jq -c '.failures // []')

if [[ -z "$task_arn" ]]; then
  err "run-task did not return a task ARN. failures: $failures"
  exit 3
fi

task_id="${task_arn##*/}"
printf 'task started: %s\n' "$task_id"

stream_name=""
if [[ -n "$log_group" && -n "$log_prefix" ]]; then
  stream_name="${log_prefix}/${container_name}/${task_id}"
  printf 'log stream: %s\n' "$stream_name"
fi

# Wait for the task to leave PROVISIONING / PENDING.
deadline=$(( $(date +%s) + STARTUP_TIMEOUT_S ))
last_status=""
while :; do
  desc=$(aws_q ecs describe-tasks \
    --cluster "$CLUSTER" \
    --tasks "$task_arn" \
    --query 'tasks[0]' \
    --output json)
  last_status=$(printf '%s' "$desc" | jq -r '.lastStatus')
  if [[ "$last_status" == "RUNNING" || "$last_status" == "STOPPED" ]]; then
    break
  fi
  if [[ $(date +%s) -ge $deadline ]]; then
    err "task did not reach RUNNING within ${STARTUP_TIMEOUT_S}s (last: $last_status)"
    exit 3
  fi
  sleep "$POLL_INTERVAL_S"
done

# Tail logs while the task runs; cheaper + simpler than `aws logs tail`
# follow-mode for a short-lived task and avoids needing live perms.
tail_logs_once() {
  if [[ -z "$stream_name" ]]; then
    return 0
  fi
  aws_q logs get-log-events \
    --log-group-name "$log_group" \
    --log-stream-name "$stream_name" \
    --start-from-head \
    --output json 2>/dev/null \
    | jq -r '.events[]?.message' \
    || true
}

printf '--- begin container logs (%s) ---\n' "$stream_name"
# Poll until STOPPED. Print logs each poll; duplicates are fine for a
# one-off run and avoid the bookkeeping of tracking next-tokens.
while [[ "$last_status" != "STOPPED" ]]; do
  sleep "$POLL_INTERVAL_S"
  desc=$(aws_q ecs describe-tasks \
    --cluster "$CLUSTER" \
    --tasks "$task_arn" \
    --query 'tasks[0]' \
    --output json)
  last_status=$(printf '%s' "$desc" | jq -r '.lastStatus')
done
tail_logs_once
printf '--- end container logs ---\n'

# Final task description for exit code + stop reason.
final=$(aws_q ecs describe-tasks \
  --cluster "$CLUSTER" \
  --tasks "$task_arn" \
  --query 'tasks[0]' \
  --output json)

exit_code=$(printf '%s' "$final" | jq -r '.containers[0].exitCode // empty')
stop_reason=$(printf '%s' "$final" | jq -r '.stoppedReason // empty')
container_reason=$(printf '%s' "$final" | jq -r '.containers[0].reason // empty')

printf 'task stopped. exit_code=%s stop_reason=%s container_reason=%s\n' \
  "${exit_code:-<none>}" "${stop_reason:-<none>}" "${container_reason:-<none>}"

if [[ -z "$exit_code" ]]; then
  # No exit code means the container never ran (image pull failure,
  # essential-container ENI attach failure, etc.). Surface as 3 (startup).
  exit 3
fi

if [[ "$exit_code" -ne 0 ]]; then
  exit "$exit_code"
fi

exit 0

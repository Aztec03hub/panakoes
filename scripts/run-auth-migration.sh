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
#   AWS_PROFILE          (no default; the script exits 2 with a clear
#                         message if unset rather than letting the AWS
#                         CLI surface a less specific credential error)
# Optional env:
#   AWS_REGION           default us-east-1
#   CLUSTER              default panakoes-dev
#   SERVICE              default panakoes-dev-auth
#   POLL_INTERVAL_S      default 5
#   STARTUP_TIMEOUT_S    default 300
#   TASK_DEFINITION      override the task definition the run-task uses.
#                        When unset the script falls back to discovering
#                        the task definition from the running service.
#                        Required when the service has
#                        lifecycle.ignore_changes = [task_definition] and
#                        a newer revision (with `dist/migrate.js` baked in)
#                        was just registered by a fresh terraform apply.
#                        Same effect as the --task-definition CLI flag.
#
# CLI flags:
#   --task-definition <family[:revision] | arn>
#                        Same as TASK_DEFINITION env var; flag wins if both
#                        are set.
#   -h | --help          Print usage and exit 0.
#
# Exit codes:
#   0  migration container exited 0
#   1  migration container exited non-zero (exit code surfaced verbatim)
#   2  bad usage / required env missing / dependency missing
#   3  task failed to start (capacity, networking, image pull, etc.)
set -euo pipefail
# pipefail ensures a failure in a piped command (e.g. `aws ... | jq ...`)
# propagates the non-zero exit instead of being masked by jq's success.

usage() {
  cat <<'USAGE'
Usage: run-auth-migration.sh [--task-definition <td>] [--help]

Launches a one-off ECS Fargate task that runs `node dist/migrate.js`
against the auth service's database. Reuses the auth task definition,
discovers networking from the running ECS service, and tails CloudWatch
logs until the container exits.

Required env:
  AWS_PROFILE              named profile with ECS + logs read perms

Optional env:
  AWS_REGION               default us-east-1
  CLUSTER                  default panakoes-dev
  SERVICE                  default panakoes-dev-auth
  POLL_INTERVAL_S          default 5
  STARTUP_TIMEOUT_S        default 300
  TASK_DEFINITION          family[:revision] or full ARN; overrides the
                           service-discovered task definition. Use this
                           when the auth service's
                           lifecycle.ignore_changes = [task_definition]
                           is pinning it to an older revision than the
                           one carrying dist/migrate.js.

Flags:
  --task-definition <td>   same as TASK_DEFINITION env (flag wins)
  -h, --help               this message

Exit codes:
  0  migration container exited 0
  1  migration container exited non-zero (code surfaced verbatim)
  2  bad usage / required env / dependency missing
  3  task failed to start (capacity, networking, image pull, etc.)
USAGE
}

# CLI arg parsing. Keep simple; only two flags today.
TASK_DEFINITION_FLAG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-definition)
      if [[ $# -lt 2 ]]; then
        printf '%s\n' "--task-definition requires a value" >&2
        exit 2
      fi
      TASK_DEFINITION_FLAG="$2"
      shift 2
      ;;
    --task-definition=*)
      TASK_DEFINITION_FLAG="${1#--task-definition=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '%s\n' "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

AWS_REGION="${AWS_REGION:-us-east-1}"
CLUSTER="${CLUSTER:-panakoes-dev}"
SERVICE="${SERVICE:-panakoes-dev-auth}"
POLL_INTERVAL_S="${POLL_INTERVAL_S:-5}"
STARTUP_TIMEOUT_S="${STARTUP_TIMEOUT_S:-300}"
# Flag overrides env. The env var path keeps the wrapper script-call-safe
# from CI / cron without flag plumbing; the flag path keeps interactive
# invocations cleaner than `TASK_DEFINITION=... ./run-auth-migration.sh`.
TASK_DEFINITION_OVERRIDE="${TASK_DEFINITION_FLAG:-${TASK_DEFINITION:-}}"

# Use `printf '%s\n' "$msg"` (never `printf "$msg"`) so messages that
# begin with `--` or contain `%` do not trip printf's option parser or
# format-spec consumption. The bug we hit in production: the log group
# prefix banner started with `---` and printf rejected `--` as an
# invalid option, dropping the banner line.
log() {
  printf '%s\n' "$*"
}

err() {
  printf '%s\n' "$*" >&2
}

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    err "missing dependency: $1"
    exit 2
  fi
}

need aws
need jq

if [[ -z "${AWS_PROFILE:-}" ]]; then
  err "AWS_PROFILE is required (set it in your shell, e.g. export AWS_PROFILE=panakoes-admin). The script exits early rather than letting the AWS CLI surface a less specific credential error."
  exit 2
fi

export AWS_PROFILE AWS_REGION

aws_q() {
  # Wraps the aws CLI with --no-cli-pager so output is never paged when
  # this script runs unattended (CI, cron).
  aws --no-cli-pager "$@"
}

log "discovering network config from service $CLUSTER/$SERVICE..."

svc_json=$(aws_q ecs describe-services \
  --cluster "$CLUSTER" \
  --services "$SERVICE" \
  --query 'services[0]' \
  --output json)

if [[ -z "$svc_json" || "$svc_json" == "null" ]]; then
  err "could not describe service $SERVICE in cluster $CLUSTER"
  exit 3
fi

discovered_task_def=$(printf '%s' "$svc_json" | jq -r '.taskDefinition')
subnets=$(printf '%s' "$svc_json" | jq -r '.networkConfiguration.awsvpcConfiguration.subnets | join(",")')
security_groups=$(printf '%s' "$svc_json" | jq -r '.networkConfiguration.awsvpcConfiguration.securityGroups | join(",")')
assign_public=$(printf '%s' "$svc_json" | jq -r '.networkConfiguration.awsvpcConfiguration.assignPublicIp // "DISABLED"')
launch_type=$(printf '%s' "$svc_json" | jq -r '.launchType // "FARGATE"')
platform_version=$(printf '%s' "$svc_json" | jq -r '.platformVersion // "LATEST"')

if [[ -z "$discovered_task_def" || "$discovered_task_def" == "null" || -z "$subnets" || -z "$security_groups" ]]; then
  err "service describe did not return required network fields"
  exit 3
fi

# Resolve the effective task definition. Override path (flag or env) wins
# over service-discovered. This is the fix for the
# lifecycle.ignore_changes = [task_definition] case: the service stays on
# an older revision, terraform apply registers a newer one with the
# migration binary, and the service-discovered revision points at the
# now-inactive older one. The override lets the operator pin the run-task
# to the freshly-registered revision (or any specific revision) without
# touching the running service.
if [[ -n "$TASK_DEFINITION_OVERRIDE" ]]; then
  task_def="$TASK_DEFINITION_OVERRIDE"
  log "using --task-definition override: $task_def (service is on $discovered_task_def)"
else
  task_def="$discovered_task_def"
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

log "using task def: $task_def"
log "container: $container_name, log group: $log_group, prefix: $log_prefix"

overrides=$(jq -nc \
  --arg name "$container_name" \
  '{containerOverrides: [{name: $name, command: ["node", "dist/migrate.js"]}]}')

network_config=$(jq -nc \
  --arg subnets "$subnets" \
  --arg sgs "$security_groups" \
  --arg pub "$assign_public" \
  '{awsvpcConfiguration: {subnets: ($subnets | split(",")), securityGroups: ($sgs | split(",")), assignPublicIp: $pub}}')

log "launching one-off migration task..."
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
log "task started: $task_id"

stream_name=""
if [[ -n "$log_group" && -n "$log_prefix" ]]; then
  stream_name="${log_prefix}/${container_name}/${task_id}"
  log "log stream: $stream_name"
fi

# Wait for the task to leave PROVISIONING / PENDING. Also warn if the
# task lingers too long in either state (common symptom of a cold ECR
# cache on first deploy or a stuck ENI attach).
deadline=$(( $(date +%s) + STARTUP_TIMEOUT_S ))
stuck_warn_threshold_s=60
provisioning_since=0
pending_since=0
provisioning_warned=0
pending_warned=0
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
  now=$(date +%s)
  if [[ "$last_status" == "PROVISIONING" ]]; then
    if [[ $provisioning_since -eq 0 ]]; then
      provisioning_since=$now
    elif [[ $provisioning_warned -eq 0 && $((now - provisioning_since)) -ge $stuck_warn_threshold_s ]]; then
      err "WARN: task in PROVISIONING for >${stuck_warn_threshold_s}s; possible stuck ENI attach or capacity issue"
      provisioning_warned=1
    fi
  elif [[ "$last_status" == "PENDING" ]]; then
    if [[ $pending_since -eq 0 ]]; then
      pending_since=$now
    elif [[ $pending_warned -eq 0 && $((now - pending_since)) -ge $stuck_warn_threshold_s ]]; then
      err "WARN: task in PENDING for >${stuck_warn_threshold_s}s; possible cold ECR cache or slow image pull on first deploy"
      pending_warned=1
    fi
  fi
  if [[ $now -ge $deadline ]]; then
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

log "--- begin container logs ($stream_name) ---"
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
log "--- end container logs ---"

# Final task description for exit code + stop reason.
final=$(aws_q ecs describe-tasks \
  --cluster "$CLUSTER" \
  --tasks "$task_arn" \
  --query 'tasks[0]' \
  --output json)

exit_code=$(printf '%s' "$final" | jq -r '.containers[0].exitCode // empty')
stop_reason=$(printf '%s' "$final" | jq -r '.stoppedReason // empty')
container_reason=$(printf '%s' "$final" | jq -r '.containers[0].reason // empty')

log "task stopped. exit_code=${exit_code:-<none>} stop_reason=${stop_reason:-<none>} container_reason=${container_reason:-<none>}"

if [[ -z "$exit_code" ]]; then
  # No exit code means the container never ran (image pull failure,
  # essential-container ENI attach failure, etc.). Surface as 3 (startup).
  exit 3
fi

if [[ "$exit_code" -ne 0 ]]; then
  exit "$exit_code"
fi

exit 0

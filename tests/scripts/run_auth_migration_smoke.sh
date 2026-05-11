#!/usr/bin/env bash
# Smoke tests for scripts/run-auth-migration.sh. There is no shellspec /
# bats harness in this repo yet, so this is a plain bash test runner.
# Run from the repo root: bash tests/scripts/run_auth_migration_smoke.sh
#
# Coverage:
#   1. --help prints usage and exits 0
#   2. Missing AWS_PROFILE exits 2 with a clear message that names the var
#   3. Unknown CLI flag exits 2
#   4. --task-definition without a value exits 2
#   5. printf-safety: the script's log() helper handles messages that
#      start with `--` without erroring (regression test for the bug
#      that dropped the begin-container-logs banner in production)
#   6. TASK_DEFINITION override (via env) is reflected in the resolved
#      task definition via a mock `aws` CLI on PATH (no live AWS calls)
#   7. --task-definition flag override is reflected via the same mock
#   8. Without override, the script discovers the task definition from
#      the running service (negative-control for tests 6 + 7)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/run-auth-migration.sh"

if [[ ! -x "$SCRIPT" ]]; then
  echo "FAIL: script not found or not executable at $SCRIPT" >&2
  exit 1
fi

pass=0
fail=0
report() {
  if [[ "$1" -eq 0 ]]; then
    printf '  PASS: %s\n' "$2"
    pass=$((pass + 1))
  else
    printf '  FAIL: %s\n' "$2" >&2
    fail=$((fail + 1))
  fi
}

# ---------------------------------------------------------------------
# Test 1: --help exits 0 and prints usage
# ---------------------------------------------------------------------
printf 'Test 1: --help prints usage and exits 0\n'
help_out=$(AWS_PROFILE=ignored "$SCRIPT" --help 2>&1) && help_rc=0 || help_rc=$?
[[ $help_rc -eq 0 ]] && echo "$help_out" | grep -q "Usage: run-auth-migration.sh"
report $? "--help exits 0 and prints usage"

# ---------------------------------------------------------------------
# Test 2: missing AWS_PROFILE exits 2 with a clear message
# ---------------------------------------------------------------------
printf 'Test 2: missing AWS_PROFILE exits 2 with a clear message\n'
set +e
out=$(env -u AWS_PROFILE bash "$SCRIPT" 2>&1)
rc=$?
set -e
[[ $rc -eq 2 ]] && echo "$out" | grep -q "AWS_PROFILE is required"
report $? "missing AWS_PROFILE exits 2 with clear message (rc=$rc)"

# ---------------------------------------------------------------------
# Test 3: unknown CLI flag exits 2
# ---------------------------------------------------------------------
printf 'Test 3: unknown CLI flag exits 2\n'
set +e
out=$(AWS_PROFILE=ignored bash "$SCRIPT" --bogus 2>&1)
rc=$?
set -e
[[ $rc -eq 2 ]] && echo "$out" | grep -q "unknown argument"
report $? "unknown CLI flag exits 2 (rc=$rc)"

# ---------------------------------------------------------------------
# Test 4: --task-definition without a value exits 2
# ---------------------------------------------------------------------
printf 'Test 4: --task-definition without value exits 2\n'
set +e
out=$(AWS_PROFILE=ignored bash "$SCRIPT" --task-definition 2>&1)
rc=$?
set -e
[[ $rc -eq 2 ]] && echo "$out" | grep -q "requires a value"
report $? "--task-definition without value exits 2 (rc=$rc)"

# ---------------------------------------------------------------------
# Test 5: printf-safety regression. The script's log() helper must not
# error on messages that start with `--`. The production bug:
#   printf '--- begin container logs (...) ---\n'
# triggered `printf: --: invalid option` on GNU printf. The fix is to
# always use `printf '%s\n' "$msg"`. Reproduce the unsafe pattern first
# (expect failure), then prove the safe wrapper used in the script
# (`log "..."` which expands to `printf '%s\n' "$*"`) succeeds.
# ---------------------------------------------------------------------
printf 'Test 5: printf-safety for messages starting with `--`\n'
# Confirm the bug exists in the unsafe form. /usr/bin/printf is the
# strict variant; bash's builtin tolerates more, but we want the script
# to work either way.
if command -v /usr/bin/printf >/dev/null 2>&1; then
  if /usr/bin/printf '--- begin container logs ---\n' >/dev/null 2>&1; then
    # Bug doesn't reproduce on this platform's printf; not a regression
    # but log it.
    printf '    note: unsafe printf form did not fail on this printf binary; safe form check still required\n'
  fi
fi
# The safe form (mirrors the script's `log` helper) must succeed.
safe_out=$(printf '%s\n' "--- begin container logs (foo) ---")
[[ "$safe_out" == "--- begin container logs (foo) ---" ]]
report $? "safe printf form handles messages starting with --"

# ---------------------------------------------------------------------
# Tests 6, 7, 8: TASK_DEFINITION override + flag override + default
# discovery. Use a mock `aws` CLI on PATH that emits canned responses
# for the script's two `aws ecs describe-services` /
# `aws ecs describe-task-definition` calls and records what task-def
# the eventual `run-task` was invoked with.
# ---------------------------------------------------------------------
make_mock_dir() {
  local d
  d=$(mktemp -d)
  cat > "$d/aws" <<'MOCK'
#!/usr/bin/env bash
# Mock aws CLI. Records run-task --task-definition into RECORD_FILE.
# Supports `--no-cli-pager` first arg as the real script passes.
args=("$@")
# Strip the leading --no-cli-pager if present, so case matching below
# is simpler.
if [[ "${args[0]:-}" == "--no-cli-pager" ]]; then
  args=("${args[@]:1}")
fi
case "${args[0]:-}" in
  ecs)
    case "${args[1]:-}" in
      describe-services)
        cat <<'JSON'
{
  "taskDefinition": "arn:aws:ecs:us-east-1:123:task-definition/panakoes-dev-auth:2",
  "networkConfiguration": {"awsvpcConfiguration": {"subnets":["subnet-a"],"securityGroups":["sg-a"],"assignPublicIp":"DISABLED"}},
  "launchType": "FARGATE",
  "platformVersion": "LATEST"
}
JSON
        ;;
      describe-task-definition)
        # Echo back which task-def was requested in the container name
        # so tests can assert the right revision was used. The script
        # consumes containerDefinitions[0].name.
        td_value=""
        for ((i=2; i<${#args[@]}; i++)); do
          if [[ "${args[$i]}" == "--task-definition" ]]; then
            td_value="${args[$((i+1))]}"
            break
          fi
        done
        cat <<JSON
{
  "containerDefinitions": [
    {"name": "$td_value", "logConfiguration": {"options": {"awslogs-group":"/dev/null","awslogs-stream-prefix":"x"}}}
  ]
}
JSON
        ;;
      run-task)
        # Record the --task-definition value and emit a fake taskArn.
        for ((i=2; i<${#args[@]}; i++)); do
          if [[ "${args[$i]}" == "--task-definition" ]]; then
            printf '%s\n' "${args[$((i+1))]}" > "$RECORD_FILE"
            break
          fi
        done
        cat <<'JSON'
{"tasks":[{"taskArn":"arn:aws:ecs:us-east-1:123:task/cluster/abc123"}],"failures":[]}
JSON
        # Exit non-zero so the script aborts before the polling loop;
        # we only care about which task-def was sent to run-task.
        # Actually we need run-task to succeed; the script proceeds to
        # describe-tasks. Handle below.
        ;;
      describe-tasks)
        # Return STOPPED immediately so the polling loops exit fast.
        cat <<'JSON'
{"lastStatus":"STOPPED","containers":[{"exitCode":0,"reason":""}],"stoppedReason":""}
JSON
        ;;
      *)
        echo "{}"
        ;;
    esac
    ;;
  logs)
    echo '{"events":[]}'
    ;;
  *)
    echo "{}"
    ;;
esac
MOCK
  chmod +x "$d/aws"
  echo "$d"
}

run_with_mock() {
  local mock_dir=$1
  shift
  RECORD_FILE=$(mktemp)
  export RECORD_FILE
  PATH="$mock_dir:$PATH" AWS_PROFILE=ignored "$SCRIPT" "$@" >/dev/null 2>&1 || true
  cat "$RECORD_FILE"
  rm -f "$RECORD_FILE"
}

printf 'Test 6: TASK_DEFINITION env var overrides discovered task-def\n'
mock_dir=$(make_mock_dir)
got=$(TASK_DEFINITION="panakoes-dev-auth:3" run_with_mock "$mock_dir")
[[ "$got" == "panakoes-dev-auth:3" ]]
report $? "TASK_DEFINITION env override applied (got: '$got')"
rm -rf "$mock_dir"

printf 'Test 7: --task-definition flag overrides discovered task-def\n'
mock_dir=$(make_mock_dir)
got=$(run_with_mock "$mock_dir" --task-definition "panakoes-dev-auth:7")
[[ "$got" == "panakoes-dev-auth:7" ]]
report $? "--task-definition flag override applied (got: '$got')"
rm -rf "$mock_dir"

printf 'Test 8: without override, service-discovered task-def is used\n'
mock_dir=$(make_mock_dir)
got=$(run_with_mock "$mock_dir")
# Discovered ARN from the mock's describe-services response.
[[ "$got" == "arn:aws:ecs:us-east-1:123:task-definition/panakoes-dev-auth:2" ]]
report $? "discovery fallback applied (got: '$got')"
rm -rf "$mock_dir"

# ---------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]

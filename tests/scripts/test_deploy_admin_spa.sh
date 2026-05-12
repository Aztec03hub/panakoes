#!/usr/bin/env bash
# Smoke tests for scripts/deploy-admin-spa.sh. Plain-bash assertions
# (no shellspec / bats harness in this repo yet).
# Run from the repo root: bash tests/scripts/test_deploy_admin_spa.sh
#
# Coverage:
#   1. --help prints usage and exits 0
#   2. --dry-run does not invoke `aws s3 sync` or
#      `aws cloudfront create-invalidation` (verified via a mock
#      `aws` CLI on PATH that appends every invocation to a sentinel
#      file; the file must remain empty)
#   3. Missing AWS_PROFILE exits 2 with a clear message
#   4. Unknown CLI flag exits 2
#   5. --env with a junk value exits 2
#   6. --api-base-url without a value exits 2
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/deploy-admin-spa.sh"

if [[ ! -x "$SCRIPT" ]]; then
  chmod +x "$SCRIPT" 2>/dev/null || true
fi
if [[ ! -f "$SCRIPT" ]]; then
  echo "FAIL: script not found at $SCRIPT" >&2
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
help_out=$(AWS_PROFILE=ignored bash "$SCRIPT" --help 2>&1) && help_rc=0 || help_rc=$?
[[ $help_rc -eq 0 ]] && echo "$help_out" | grep -q "Usage: scripts/deploy-admin-spa.sh"
report $? "--help exits 0 and prints usage"

# ---------------------------------------------------------------------
# Test 2: --dry-run does not invoke aws s3 sync or cloudfront create-invalidation
#
# We install a mock `aws` CLI on PATH that appends its argv to a
# sentinel file. The script's `run` helper in --dry-run mode only
# `log`s the command; it must not exec.
# Also stub `terraform` and `pnpm` so the script does not fail on
# pre-flight `command -v` checks even though their bodies are gated
# behind DRY_RUN=1 branches.
# ---------------------------------------------------------------------
printf 'Test 2: --dry-run does not invoke aws s3 sync or cloudfront create-invalidation\n'
TMPDIR_T2=$(mktemp -d)
trap 'rm -rf "$TMPDIR_T2"' EXIT
SENTINEL="$TMPDIR_T2/aws-invocations.log"
: > "$SENTINEL"

mkdir -p "$TMPDIR_T2/bin"
cat >"$TMPDIR_T2/bin/aws" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$SENTINEL"
# Default success for any AWS call (none should land here in dry-run).
echo "MOCK-AWS-OK"
exit 0
EOF
cat >"$TMPDIR_T2/bin/terraform" <<'EOF'
#!/usr/bin/env bash
# Should not be invoked in dry-run mode (the script gates it on DRY_RUN).
echo "MOCK-TF-OK"
exit 0
EOF
cat >"$TMPDIR_T2/bin/pnpm" <<'EOF'
#!/usr/bin/env bash
echo "MOCK-PNPM-OK"
exit 0
EOF
chmod +x "$TMPDIR_T2/bin/aws" "$TMPDIR_T2/bin/terraform" "$TMPDIR_T2/bin/pnpm"

set +e
PATH="$TMPDIR_T2/bin:$PATH" \
  AWS_PROFILE=test-profile \
  bash "$SCRIPT" --dry-run >"$TMPDIR_T2/dryrun.out" 2>&1
dr_rc=$?
set -e

if [[ $dr_rc -ne 0 ]]; then
  err_msg="dry-run exited $dr_rc; output:\n$(cat "$TMPDIR_T2/dryrun.out")"
  printf '  DEBUG: %b\n' "$err_msg" >&2
fi

# Assertion A: sentinel must be empty (no aws calls executed).
sentinel_lines=$(wc -l <"$SENTINEL" | tr -d ' ')
if [[ "$sentinel_lines" -eq 0 && $dr_rc -eq 0 ]]; then
  rc=0
else
  rc=1
fi
report $rc "--dry-run did not exec aws (sentinel lines=$sentinel_lines, rc=$dr_rc)"

# Assertion B: dry-run output must mention the s3 sync + create-invalidation
# commands (proving the script reached those steps, just did not run them).
grep -q "aws s3 sync" "$TMPDIR_T2/dryrun.out" && \
  grep -q "aws cloudfront create-invalidation" "$TMPDIR_T2/dryrun.out"
report $? "--dry-run printed (but did not exec) the s3 + cloudfront commands"

# ---------------------------------------------------------------------
# Test 3: missing AWS_PROFILE exits 2 with a clear message
# ---------------------------------------------------------------------
printf 'Test 3: missing AWS_PROFILE exits 2 with a clear message\n'
set +e
out=$(env -u AWS_PROFILE bash "$SCRIPT" 2>&1)
rc=$?
set -e
[[ $rc -eq 2 ]] && echo "$out" | grep -q "AWS_PROFILE is required"
report $? "missing AWS_PROFILE exits 2 with clear message (rc=$rc)"

# ---------------------------------------------------------------------
# Test 4: unknown flag exits 2
# ---------------------------------------------------------------------
printf 'Test 4: unknown flag exits 2\n'
set +e
AWS_PROFILE=test bash "$SCRIPT" --bogus-flag >/dev/null 2>&1
rc=$?
set -e
[[ $rc -eq 2 ]]
report $? "--bogus-flag exits 2 (rc=$rc)"

# ---------------------------------------------------------------------
# Test 5: --env junk exits 2
# ---------------------------------------------------------------------
printf 'Test 5: --env junk exits 2\n'
set +e
AWS_PROFILE=test bash "$SCRIPT" --env staging >/dev/null 2>&1
rc=$?
set -e
[[ $rc -eq 2 ]]
report $? "--env staging exits 2 (rc=$rc)"

# ---------------------------------------------------------------------
# Test 6: --api-base-url without value exits 2
# ---------------------------------------------------------------------
printf 'Test 6: --api-base-url without a value exits 2\n'
set +e
AWS_PROFILE=test bash "$SCRIPT" --api-base-url 2>/dev/null
rc=$?
set -e
[[ $rc -eq 2 ]]
report $? "--api-base-url without value exits 2 (rc=$rc)"

# ---------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------
printf '\n%d passed, %d failed\n' "$pass" "$fail"
[[ $fail -eq 0 ]]

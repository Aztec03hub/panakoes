#!/usr/bin/env bash
# tests/hooks/test_pre_push.sh: smoke-tests the .githooks/pre-push hook.
#
# Verifies three independent behaviors:
#   1. NO_VERIFY=1 short-circuits and exits 0 without invoking make.
#   2. A failing `make ci-pr` propagates a non-zero exit from the hook.
#   3. A make invocation that exceeds _PREPUSH_TIMEOUT_S exits non-zero
#      and emits the actionable budget message.
#
# We inject a fake make via _PREPUSH_MAKE_BIN (a hook seam) rather than
# shadowing PATH; that way the test stays self-contained and doesn't
# depend on shell precedence quirks.
#
# Usage: bash tests/hooks/test_pre_push.sh
# Exit: 0 if all assertions pass, 1 on first failure.

set -u

# Resolve repo root regardless of CWD when invoked.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/../.." &>/dev/null && pwd)
HOOK="$REPO_ROOT/.githooks/pre-push"

if [ ! -x "$HOOK" ]; then
  echo "FAIL: hook not executable at $HOOK" >&2
  exit 1
fi

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

pass=0
fail=0

assert() {
  local name="$1" actual="$2" expected="$3"
  if [ "$actual" = "$expected" ]; then
    echo "  PASS: $name (rc=$actual)"
    pass=$(( pass + 1 ))
  else
    echo "  FAIL: $name (expected rc=$expected, got rc=$actual)" >&2
    fail=$(( fail + 1 ))
  fi
}

assert_contains() {
  local name="$1" haystack="$2" needle="$3"
  if printf '%s' "$haystack" | grep -qF "$needle"; then
    echo "  PASS: $name (output contains '$needle')"
    pass=$(( pass + 1 ))
  else
    echo "  FAIL: $name (output missing '$needle')" >&2
    echo "----- actual output -----" >&2
    printf '%s\n' "$haystack" >&2
    echo "-------------------------" >&2
    fail=$(( fail + 1 ))
  fi
}

# ----------------------------------------------------------------------
# Test 1: NO_VERIFY=1 short-circuits and exits 0.
# ----------------------------------------------------------------------
echo "Test 1: NO_VERIFY=1 short-circuits"
fake_make_unused="$TMPDIR_TEST/make-unused"
cat >"$fake_make_unused" <<'EOF'
#!/usr/bin/env bash
echo "FAKE MAKE INVOKED" >&2
exit 99
EOF
chmod +x "$fake_make_unused"

set +e
out1=$( NO_VERIFY=1 _PREPUSH_MAKE_BIN="$fake_make_unused" "$HOOK" 2>&1 )
rc1=$?
set -e
assert "NO_VERIFY=1 exits 0" "$rc1" "0"
assert_contains "NO_VERIFY=1 prints skip warning" "$out1" "SKIPPING pre-push checks"
# And critically: the fake make was not invoked.
if printf '%s' "$out1" | grep -qF "FAKE MAKE INVOKED"; then
  echo "  FAIL: NO_VERIFY=1 still invoked make" >&2
  fail=$(( fail + 1 ))
else
  echo "  PASS: NO_VERIFY=1 did not invoke make"
  pass=$(( pass + 1 ))
fi

# ----------------------------------------------------------------------
# Test 2: failing make propagates non-zero.
# ----------------------------------------------------------------------
echo ""
echo "Test 2: failing 'make ci-pr' propagates non-zero exit"
fake_make_fail="$TMPDIR_TEST/make-fail"
cat >"$fake_make_fail" <<'EOF'
#!/usr/bin/env bash
# Honor the -n dry-run probe so the hook can detect ci-pr is "available".
if [ "${1:-}" = "-n" ]; then
  exit 0
fi
echo "fake make: simulated failure" >&2
exit 7
EOF
chmod +x "$fake_make_fail"

set +e
out2=$( _PREPUSH_MAKE_BIN="$fake_make_fail" "$HOOK" 2>&1 )
rc2=$?
set -e
# We expect the hook to surface the fake make's exit code (7).
if [ "$rc2" -ne 0 ]; then
  echo "  PASS: hook exited non-zero on make failure (rc=$rc2)"
  pass=$(( pass + 1 ))
else
  echo "  FAIL: hook exited 0 despite failing make" >&2
  fail=$(( fail + 1 ))
fi
assert_contains "failure message names the log file" "$out2" "Full log:"

# ----------------------------------------------------------------------
# Test 3: timeout path triggers actionable message.
# Use _PREPUSH_TIMEOUT_S=2 + a fake make that sleeps past it.
# ----------------------------------------------------------------------
echo ""
echo "Test 3: _PREPUSH_TIMEOUT_S=2 triggers on a slow make"
fake_make_slow="$TMPDIR_TEST/make-slow"
cat >"$fake_make_slow" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = "-n" ]; then
  exit 0
fi
sleep 10
EOF
chmod +x "$fake_make_slow"

set +e
out3=$( _PREPUSH_TIMEOUT_S=2 _PREPUSH_MAKE_BIN="$fake_make_slow" "$HOOK" 2>&1 )
rc3=$?
set -e
if [ "$rc3" -ne 0 ]; then
  echo "  PASS: hook exited non-zero on timeout (rc=$rc3)"
  pass=$(( pass + 1 ))
else
  echo "  FAIL: hook exited 0 despite timeout" >&2
  fail=$(( fail + 1 ))
fi
assert_contains "timeout message mentions budget" "$out3" "exceeded"
assert_contains "timeout message mentions NO_VERIFY escape" "$out3" "NO_VERIFY=1"

# ----------------------------------------------------------------------
echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]

#!/usr/bin/env bash
# Tests for scripts/verify-agent-run.sh.
#
# Each test sets up a temp directory that LOOKS like a worktree (it gets a
# real `git init`, a base commit, a feature branch, and synthetic
# .agent-runs/ contents) and invokes the script with controlled inputs.
#
# Test cases:
#   1. clean run: all checks pass (exit 0)
#   2. missing run report: check 1 fails (exit 10)
#   3. em-dash in diff: check 4 fails (exit 12)
#   4. gitleaks pattern match: check 5 fails (exit 13) -- gracefully skipped if gitleaks missing
#   5. progress log ends with BLOCKED: check 6 fails (exit 14)
#   6. non-conventional commit: check 7 fails (exit 15)
#   7. missing Local-First Verification section: check 8 fails (exit 16)
#   8. report status != success: short-circuits with exit 10

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)
SCRIPT="$REPO_ROOT/scripts/verify-agent-run.sh"

TEST_PASS=0
TEST_FAIL=0
FAILED_TESTS=()

pass() {
    printf "  [PASS] %s\n" "$1"
    TEST_PASS=$((TEST_PASS + 1))
}

fail() {
    printf "  [FAIL] %s\n" "$1"
    printf "         %s\n" "${2:-}"
    TEST_FAIL=$((TEST_FAIL + 1))
    FAILED_TESTS+=("$1")
}

# Build a synthetic worktree at $1 with the standard structure.
# Optional flags via env vars set by caller:
#   INCLUDE_REPORT=1  (default 1): drop a valid run report
#   REPORT_STATUS=success | failure
#   INCLUDE_PROGRESS=1 (default 1)
#   PROGRESS_LAST_LINE: override final line of progress log
#   ADD_EM_DASH=0|1
#   ADD_GITLEAKS_TRIGGER=0|1
#   COMMIT_MSG (default conventional)
#   INCLUDE_LOCAL_FIRST=1 (default 1)
#   FILES_MATCH=1 (default 1): if 0, report claims a file not in diff
make_worktree() {
    local dir="$1"
    rm -rf "$dir"
    mkdir -p "$dir"
    cd "$dir"
    git init -q -b main
    git config user.email "test@test.test"
    git config user.name "Test"

    # base commit on main
    echo "base" > base.txt
    git add base.txt
    git commit -q -m "chore: initial commit"

    # Set up an origin/main ref pointing at the base
    git update-ref refs/remotes/origin/main HEAD

    # New branch
    git checkout -q -b feat/test-branch

    # Touched file in the diff
    if [ "${ADD_EM_DASH:-0}" = "1" ]; then
        # U+2014 em dash literal in the new file
        printf 'hello \xe2\x80\x94 world\n' > new.txt
    else
        echo "new feature content" > new.txt
    fi

    if [ "${ADD_GITLEAKS_TRIGGER:-0}" = "1" ]; then
        # Write a realistic-looking AWS access key + secret pair to trip
        # gitleaks. Gitleaks v8 stoplists common 'EXAMPLE' fixtures, so we
        # use a synthetic-but-not-stoplisted pattern. These are not real
        # credentials; they exist solely to exercise the detection path.
        printf 'aws_access_key_id = AKIA2E0A8F3B244C9986\naws_secret_access_key = j7NcGbB+8sYZ1Q3pK2vRfL5tHnXmDqW8ZuA4ePcM\n' > secrets.txt
        git add secrets.txt
    fi

    git add new.txt

    local commit_msg="${COMMIT_MSG:-feat(scripts): add the test thing}"
    git commit -q -m "$commit_msg"

    # Build the .agent-runs directory
    mkdir -p .agent-runs

    if [ "${INCLUDE_REPORT:-1}" = "1" ]; then
        local status="${REPORT_STATUS:-success}"
        local files_modified_yaml="  - new.txt"
        if [ "${FILES_MATCH:-1}" = "0" ]; then
            files_modified_yaml="  - imaginary.txt"
        fi
        if [ "${ADD_GITLEAKS_TRIGGER:-0}" = "1" ]; then
            files_modified_yaml="$files_modified_yaml"$'\n'"  - secrets.txt"
        fi
        local local_first_section=""
        if [ "${INCLUDE_LOCAL_FIRST:-1}" = "1" ]; then
            local_first_section=$'\n## Local-First Verification\n\nmake ci-fast: passed in 41s\nuv run pytest: 12 passed\n'
        fi
        cat > .agent-runs/2026-05-18T20-00-00Z-test-run.md <<EOF
---
run_id: 2026-05-18T20-00-00Z-test-run
agent_description: "Test the verify script"
started_at: "2026-05-18T20:00:00Z"
finished_at: "2026-05-18T20:05:00Z"
duration_seconds: 300
status: ${status}
files_created: []
files_modified:
${files_modified_yaml}
files_deleted: []
commits_made: []
verification:
  build_clean: true
  tests_passing: true
  em_dashes: 0
---

# Test Run

## Summary
Test run.${local_first_section}
EOF
    fi

    if [ "${INCLUDE_PROGRESS:-1}" = "1" ]; then
        local last_line="${PROGRESS_LAST_LINE:-[2026-05-18T20:05:00Z] [DONE] status=success}"
        cat > .agent-runs/2026-05-18T20-00-00Z-test-run.progress.log <<EOF
[2026-05-18T20:00:00Z] [START] go
[2026-05-18T20:01:00Z] [FILES-WRITTEN] new.txt
${last_line}
EOF
    fi
}

# ---------- test 1: clean run ----------

echo ""
echo "Test 1: clean run, all checks pass"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
(make_worktree "$TMP/wt1")
out=$(bash "$SCRIPT" --worktree "$TMP/wt1" --base origin/main 2>&1)
rc=$?
if [ "$rc" -eq 0 ]; then
    pass "test 1: clean run (rc=0)"
else
    fail "test 1: clean run" "rc=$rc, output: $out"
fi

# ---------- test 2: missing run report ----------

echo ""
echo "Test 2: missing run report"
(INCLUDE_REPORT=0 make_worktree "$TMP/wt2")
out=$(bash "$SCRIPT" --worktree "$TMP/wt2" --base origin/main 2>&1)
rc=$?
if [ "$rc" -eq 10 ]; then
    pass "test 2: missing run report (rc=10)"
else
    fail "test 2: missing run report" "rc=$rc (expected 10), output: $out"
fi

# ---------- test 3: em-dash in diff ----------

echo ""
echo "Test 3: em-dash in diff"
(ADD_EM_DASH=1 make_worktree "$TMP/wt3")
out=$(bash "$SCRIPT" --worktree "$TMP/wt3" --base origin/main 2>&1)
rc=$?
if [ "$rc" -eq 12 ]; then
    pass "test 3: em-dash in diff (rc=12)"
else
    fail "test 3: em-dash in diff" "rc=$rc (expected 12), output: $out"
fi

# ---------- test 4: gitleaks pattern match ----------

echo ""
echo "Test 4: gitleaks pattern match"
if command -v gitleaks >/dev/null 2>&1; then
    (ADD_GITLEAKS_TRIGGER=1 make_worktree "$TMP/wt4")
    out=$(bash "$SCRIPT" --worktree "$TMP/wt4" --base origin/main 2>&1)
    rc=$?
    if [ "$rc" -eq 13 ]; then
        pass "test 4: gitleaks pattern match (rc=13)"
    else
        fail "test 4: gitleaks pattern match" "rc=$rc (expected 13), output: $out"
    fi
else
    echo "  [SKIP] gitleaks not on PATH"
fi

# ---------- test 5: progress log ends with BLOCKED ----------

echo ""
echo "Test 5: progress log ends with BLOCKED"
(PROGRESS_LAST_LINE="[2026-05-18T20:05:00Z] [BLOCKED] tests fail" make_worktree "$TMP/wt5")
out=$(bash "$SCRIPT" --worktree "$TMP/wt5" --base origin/main 2>&1)
rc=$?
if [ "$rc" -eq 14 ]; then
    pass "test 5: BLOCKED progress log (rc=14)"
else
    fail "test 5: BLOCKED progress log" "rc=$rc (expected 14), output: $out"
fi

# ---------- test 6: non-conventional commit ----------

echo ""
echo "Test 6: non-conventional commit subject"
(COMMIT_MSG="add the test thing (no type prefix)" make_worktree "$TMP/wt6")
out=$(bash "$SCRIPT" --worktree "$TMP/wt6" --base origin/main 2>&1)
rc=$?
if [ "$rc" -eq 15 ]; then
    pass "test 6: non-conventional commit (rc=15)"
else
    fail "test 6: non-conventional commit" "rc=$rc (expected 15), output: $out"
fi

# ---------- test 7: missing Local-First Verification section ----------

echo ""
echo "Test 7: missing Local-First Verification section"
(INCLUDE_LOCAL_FIRST=0 make_worktree "$TMP/wt7")
out=$(bash "$SCRIPT" --worktree "$TMP/wt7" --base origin/main 2>&1)
rc=$?
if [ "$rc" -eq 16 ]; then
    pass "test 7: missing Local-First Verification (rc=16)"
else
    fail "test 7: missing Local-First Verification" "rc=$rc (expected 16), output: $out"
fi

# ---------- test 8: status != success short-circuits ----------

echo ""
echo "Test 8: report status=failure short-circuits"
(REPORT_STATUS=failure make_worktree "$TMP/wt8")
out=$(bash "$SCRIPT" --worktree "$TMP/wt8" --base origin/main 2>&1)
rc=$?
if [ "$rc" -eq 10 ]; then
    pass "test 8: status=failure short-circuit (rc=10)"
else
    fail "test 8: status=failure short-circuit" "rc=$rc (expected 10), output: $out"
fi

# ---------- summary ----------

echo ""
echo "================================================================"
total=$((TEST_PASS + TEST_FAIL))
if [ "$TEST_FAIL" -eq 0 ]; then
    echo "PASS: $TEST_PASS/$total tests"
    exit 0
else
    echo "FAIL: $TEST_FAIL/$total tests failed"
    for t in "${FAILED_TESTS[@]}"; do
        echo "  - $t"
    done
    exit 1
fi

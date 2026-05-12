#!/usr/bin/env bash
# Tests for scripts/assemble-changelog.sh.
#
# Each test sets up a temp dir with a fragments folder + CHANGELOG.md,
# runs the script, asserts on stdout / file contents / exit code, and
# cleans up. Fast (< 2s total), no network, no git operations on the
# real repo.
#
# Usage:
#   bash tests/scripts/test_assemble_changelog.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
SCRIPT="$REPO_ROOT/scripts/assemble-changelog.sh"

PASS=0
FAIL=0
FAILED_TESTS=()

mktmp() { mktemp -d -t assemble-changelog.XXXXXX; }

assert_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    return 0
  fi
  echo "  FAIL [$label]: expected to find:"
  echo "    $needle"
  echo "  in:"
  echo "$haystack" | sed 's/^/    /'
  return 1
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  local label="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    return 0
  fi
  echo "  FAIL [$label]: expected NOT to find:"
  echo "    $needle"
  return 1
}

run_test() {
  local name="$1"
  shift
  printf "TEST: %s ... " "$name"
  if "$@"; then
    echo "PASS"
    PASS=$((PASS + 1))
  else
    echo "FAIL"
    FAIL=$((FAIL + 1))
    FAILED_TESTS+=("$name")
  fi
}

# --- test 1: dry-run on a happy-path mix of fragments emits all six categories in canonical order ---
test_dry_run_canonical_order() {
  local dir
  dir="$(mktmp)"
  mkdir -p "$dir/.changelog"

  cat > "$dir/.changelog/20260101T000000Z-a.md" <<'EOF'
---
category: Fixed
---

- `services/a`: fix one
EOF

  cat > "$dir/.changelog/20260101T000001Z-b.md" <<'EOF'
---
category: Added
---

- `services/b`: add one
EOF

  cat > "$dir/.changelog/20260101T000002Z-c.md" <<'EOF'
---
category: Security
---

- `services/c`: security one
EOF

  local out
  out="$(bash "$SCRIPT" --fragments-dir "$dir/.changelog" --version v9.9.9 --dry-run)"

  # Categories should appear in canonical order Added -> ... -> Security
  local added_pos changed_pos fixed_pos security_pos
  added_pos="$(printf '%s' "$out" | grep -n '^### Added$' | cut -d: -f1)"
  fixed_pos="$(printf '%s' "$out" | grep -n '^### Fixed$' | cut -d: -f1)"
  security_pos="$(printf '%s' "$out" | grep -n '^### Security$' | cut -d: -f1)"

  if [ -z "$added_pos" ] || [ -z "$fixed_pos" ] || [ -z "$security_pos" ]; then
    echo "  FAIL: missing one or more category headers"
    rm -rf "$dir"
    return 1
  fi

  if ! { [ "$added_pos" -lt "$fixed_pos" ] && [ "$fixed_pos" -lt "$security_pos" ]; }; then
    echo "  FAIL: categories out of order. Added=$added_pos Fixed=$fixed_pos Security=$security_pos"
    rm -rf "$dir"
    return 1
  fi

  assert_contains "$out" '## [v9.9.9] -' "version header" || { rm -rf "$dir"; return 1; }
  assert_contains "$out" 'services/a' "fixed bullet" || { rm -rf "$dir"; return 1; }
  assert_contains "$out" 'services/b' "added bullet" || { rm -rf "$dir"; return 1; }
  assert_contains "$out" 'services/c' "security bullet" || { rm -rf "$dir"; return 1; }
  # Empty categories must NOT appear.
  assert_not_contains "$out" '### Deprecated' "no empty Deprecated" || { rm -rf "$dir"; return 1; }
  assert_not_contains "$out" '### Removed' "no empty Removed" || { rm -rf "$dir"; return 1; }
  assert_not_contains "$out" '### Changed' "no empty Changed" || { rm -rf "$dir"; return 1; }

  rm -rf "$dir"
  return 0
}

# --- test 2: bullets within a category sort by filename (UTC-timestamp ascending) ---
test_bullets_stable_order() {
  local dir
  dir="$(mktmp)"
  mkdir -p "$dir/.changelog"

  cat > "$dir/.changelog/20260301T000000Z-second.md" <<'EOF'
---
category: Added
---

- second
EOF

  cat > "$dir/.changelog/20260101T000000Z-first.md" <<'EOF'
---
category: Added
---

- first
EOF

  local out
  out="$(bash "$SCRIPT" --fragments-dir "$dir/.changelog" --version v0.0.1 --dry-run)"

  local first_pos second_pos
  first_pos="$(printf '%s' "$out" | grep -n '^- first$' | cut -d: -f1)"
  second_pos="$(printf '%s' "$out" | grep -n '^- second$' | cut -d: -f1)"

  if [ -z "$first_pos" ] || [ -z "$second_pos" ]; then
    echo "  FAIL: missing bullets"
    rm -rf "$dir"
    return 1
  fi

  if [ "$first_pos" -ge "$second_pos" ]; then
    echo "  FAIL: 'first' should appear before 'second'. first=$first_pos second=$second_pos"
    rm -rf "$dir"
    return 1
  fi

  rm -rf "$dir"
  return 0
}

# --- test 3: --prune deletes the consumed fragment files ---
test_prune_deletes_fragments() {
  local dir
  dir="$(mktmp)"
  mkdir -p "$dir/.changelog"

  cat > "$dir/.changelog/20260101T000000Z-foo.md" <<'EOF'
---
category: Added
---

- foo
EOF

  cat > "$dir/CHANGELOG.md" <<'EOF'
# Changelog

## [Unreleased]

## [v0.0.1] - 2026-01-01

### Added
- bootstrap
EOF

  bash "$SCRIPT" \
    --fragments-dir "$dir/.changelog" \
    --changelog "$dir/CHANGELOG.md" \
    --version v0.0.2 \
    --prune > /dev/null

  if [ -f "$dir/.changelog/20260101T000000Z-foo.md" ]; then
    echo "  FAIL: fragment was not pruned"
    rm -rf "$dir"
    return 1
  fi

  local body
  body="$(cat "$dir/CHANGELOG.md")"
  assert_contains "$body" '## [v0.0.2]' "new version header" || { rm -rf "$dir"; return 1; }
  assert_contains "$body" '- foo' "new bullet" || { rm -rf "$dir"; return 1; }
  assert_contains "$body" '## [v0.0.1]' "preserves prior version" || { rm -rf "$dir"; return 1; }
  assert_contains "$body" '## [Unreleased]' "preserves Unreleased marker" || { rm -rf "$dir"; return 1; }

  rm -rf "$dir"
  return 0
}

# --- test 4: malformed fragment (missing category) exits 3 ---
test_missing_category_rejected() {
  local dir
  dir="$(mktmp)"
  mkdir -p "$dir/.changelog"

  cat > "$dir/.changelog/20260101T000000Z-bad.md" <<'EOF'
---
---

- no category
EOF

  set +e
  bash "$SCRIPT" --fragments-dir "$dir/.changelog" --version v0.0.1 --dry-run > /dev/null 2>&1
  local rc=$?
  set -e

  rm -rf "$dir"
  if [ "$rc" -ne 3 ]; then
    echo "  FAIL: expected exit 3 on missing category, got $rc"
    return 1
  fi
  return 0
}

# --- test 5: unknown category rejected ---
test_unknown_category_rejected() {
  local dir
  dir="$(mktmp)"
  mkdir -p "$dir/.changelog"

  cat > "$dir/.changelog/20260101T000000Z-bad.md" <<'EOF'
---
category: Vibes
---

- not a real category
EOF

  set +e
  bash "$SCRIPT" --fragments-dir "$dir/.changelog" --version v0.0.1 --dry-run > /dev/null 2>&1
  local rc=$?
  set -e

  rm -rf "$dir"
  if [ "$rc" -ne 3 ]; then
    echo "  FAIL: expected exit 3 on unknown category, got $rc"
    return 1
  fi
  return 0
}

# --- test 6: empty fragments dir exits 1 ---
test_empty_dir_exits_1() {
  local dir
  dir="$(mktmp)"
  mkdir -p "$dir/.changelog"
  # Just a README, no fragments.
  echo "README only" > "$dir/.changelog/README.md"

  set +e
  bash "$SCRIPT" --fragments-dir "$dir/.changelog" --version v0.0.1 --dry-run > /dev/null 2>&1
  local rc=$?
  set -e

  rm -rf "$dir"
  if [ "$rc" -ne 1 ]; then
    echo "  FAIL: expected exit 1 on empty fragments, got $rc"
    return 1
  fi
  return 0
}

# --- test 7: write splices above the next versioned section ---
test_splice_position() {
  local dir
  dir="$(mktmp)"
  mkdir -p "$dir/.changelog"

  cat > "$dir/.changelog/20260101T000000Z-foo.md" <<'EOF'
---
category: Added
---

- new entry
EOF

  cat > "$dir/CHANGELOG.md" <<'EOF'
# Changelog

## [Unreleased]

## [v0.0.1] - 2026-01-01

### Added
- prior entry
EOF

  bash "$SCRIPT" \
    --fragments-dir "$dir/.changelog" \
    --changelog "$dir/CHANGELOG.md" \
    --version v0.0.2 > /dev/null

  # Assert the new block appears between Unreleased and v0.0.1.
  local body unreleased_line new_line prior_line
  body="$(cat "$dir/CHANGELOG.md")"
  unreleased_line="$(printf '%s' "$body" | grep -n '^## \[Unreleased\]' | cut -d: -f1)"
  new_line="$(printf '%s' "$body" | grep -n '^## \[v0.0.2\]' | cut -d: -f1)"
  prior_line="$(printf '%s' "$body" | grep -n '^## \[v0.0.1\]' | cut -d: -f1)"

  if [ -z "$unreleased_line" ] || [ -z "$new_line" ] || [ -z "$prior_line" ]; then
    echo "  FAIL: missing one of the expected headers"
    rm -rf "$dir"
    return 1
  fi

  if ! { [ "$unreleased_line" -lt "$new_line" ] && [ "$new_line" -lt "$prior_line" ]; }; then
    echo "  FAIL: splice order wrong. Unreleased=$unreleased_line v0.0.2=$new_line v0.0.1=$prior_line"
    rm -rf "$dir"
    return 1
  fi

  rm -rf "$dir"
  return 0
}

run_test "dry-run emits canonical Keep a Changelog order" test_dry_run_canonical_order
run_test "bullets within a category sort by filename" test_bullets_stable_order
run_test "--prune deletes consumed fragments and writes CHANGELOG" test_prune_deletes_fragments
run_test "missing category exits 3" test_missing_category_rejected
run_test "unknown category exits 3" test_unknown_category_rejected
run_test "empty fragments dir exits 1" test_empty_dir_exits_1
run_test "splice lands between [Unreleased] and the previous version" test_splice_position

echo
echo "Results: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  for t in "${FAILED_TESTS[@]}"; do
    echo "  FAILED: $t"
  done
  exit 1
fi
exit 0

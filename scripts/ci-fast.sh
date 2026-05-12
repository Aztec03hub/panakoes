#!/usr/bin/env bash
# ci-fast.sh: the sub-90-second pre-push gate.
#
# Why this exists:
#   `make ci-pr` (the prior pre-push gate) routinely exceeded 8 minutes on
#   multi-service PRs and pushed contributors toward `NO_VERIFY=1`, which
#   itself leaked an em-dash to main in PR #232 (cleaned up in PR #242).
#   The honest framing: the server-side CI is the canonical merge gate;
#   the pre-push hook should catch the silly stuff fast (secrets, em-dashes,
#   broken workflow YAML, unformatted Terraform) and let the rest run on
#   GitHub Actions where wall-clock does not block the push.
#
# What this runs (always, against changed paths only):
#   1. gitleaks on the staged diff
#   2. scripts/check_no_em_dashes.sh on changed files
#   3. actionlint on changed .github/workflows/*.yml
#   4. terraform fmt -check on changed infra/**/*.tf
#
# What this runs (conditionally, path-scoped, 30s max each):
#   5. ruff check on changed *.py paths only (no whole-repo sweep)
#   6. terraform validate in modules with changed *.tf only
#   7. (TS biome is conditional but deferred to server-side until we have
#      a reliable path-scoped invocation; see CI-FULL below.)
#
# What this never runs:
#   - pytest, vitest (server-side CI gates these)
#   - mypy --strict (slow on cold cache; server-side gates this)
#   - pre-commit run --all-files (server-side gates this)
#   - pnpm install / uv sync (network + minutes)
#
# Escape hatches:
#   - NO_VERIFY=1 git push       skip the hook entirely (documented; rare)
#   - CI_FULL=1 make ci-fast     run the full ci-pr behavior instead
#   - make ci-full               explicit alias for ci-pr (server-side mirror)
#
# Target wall-clock: <= 90 seconds. If a phase grows past that, push it
# server-side rather than tax every push.
#
# See feedback_ci_fast_pre_push.md for the design rationale and the
# server-side-is-canonical contract.

set -euo pipefail

# CI_FULL escape: run the full ci-pr instead. Useful before tagging a
# release or when a contributor wants the kitchen-sink locally.
if [ "${CI_FULL:-0}" = "1" ]; then
  echo "[ci-fast] CI_FULL=1 set; delegating to scripts/ci-pr.sh"
  exec "$(dirname "$0")/ci-pr.sh"
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
  echo "ci-fast: not inside a git repo" >&2
  exit 2
fi
cd "$REPO_ROOT"

BASE_REF="${BASE_REF:-origin/main}"

# Refresh the base ref. Don't fail if offline; the merge-base still
# resolves against the local copy of origin/main.
git fetch origin main --quiet 2>/dev/null || true

if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
  echo "ci-fast: base ref '$BASE_REF' does not exist; pass BASE_REF=<ref>" >&2
  exit 2
fi

HEAD_SHA=$(git rev-parse HEAD)
BASE_SHA=$(git rev-parse "$BASE_REF")
if [ "$HEAD_SHA" = "$BASE_SHA" ]; then
  CMP_BASE="HEAD~1"
else
  CMP_BASE=$(git merge-base "$BASE_REF" HEAD)
fi

# `git diff $CMP_BASE` (no HEAD on the right side) so the working tree
# counts too: mid-edit `make ci-fast` runs see unstaged edits, and
# pre-push (post-commit) sees the same set as the about-to-push HEAD.
# Untracked-but-not-ignored files are added via ls-files so a brand-new
# script the dev hasn't `git add`ed yet still gets scanned.
CHANGED=$( ( git diff --name-only "$CMP_BASE"; git ls-files --others --exclude-standard ) | sort -u )

if [ -z "$CHANGED" ]; then
  echo "ci-fast: no files changed vs $BASE_REF; nothing to gate. Push away."
  exit 0
fi

echo "ci-fast: comparing $CMP_BASE..HEAD"
file_count=$(echo "$CHANGED" | wc -l)
echo "ci-fast: $file_count files changed"
echo ""

# Build path buckets in a single pass.
PY_FILES=()
TF_DIRS=()
WF_FILES=()
SCAN_FILES=()  # files that exist on disk (excludes deletions) for em-dash + gitleaks

declare -A TF_DIR_SEEN=()

while IFS= read -r f; do
  [ -z "$f" ] && continue
  if [ -f "$f" ]; then
    SCAN_FILES+=("$f")
  fi
  case "$f" in
    *.py)
      [ -f "$f" ] && PY_FILES+=("$f")
      ;;
    infra/*.tf|infra/**/*.tf)
      if [ -f "$f" ]; then
        d=$(dirname "$f")
        if [ -z "${TF_DIR_SEEN[$d]:-}" ]; then
          TF_DIRS+=("$d")
          TF_DIR_SEEN[$d]=1
        fi
      fi
      ;;
    .github/workflows/*.yml|.github/workflows/*.yaml)
      [ -f "$f" ] && WF_FILES+=("$f")
      ;;
  esac
done <<< "$CHANGED"

start_ts=$(date +%s)

# 1. gitleaks on the diff (always; ~2-5s).
# We use `pre-commit run gitleaks --files <list>` so we share the pinned
# version from .pre-commit-config.yaml without needing a separate gitleaks
# binary installed. Falls back gracefully if pre-commit is missing.
if [ ${#SCAN_FILES[@]} -gt 0 ]; then
  if command -v pre-commit >/dev/null 2>&1; then
    echo "==> [1/5] gitleaks (changed files)"
    pre-commit run gitleaks --files "${SCAN_FILES[@]}"
  else
    echo "[ci-fast] WARN: pre-commit not on PATH; skipping gitleaks. Server-side CI will catch leaks."
  fi
fi

# 2. em-dash detector (always; <1s).
if [ ${#SCAN_FILES[@]} -gt 0 ]; then
  echo "==> [2/5] em-dash detector (changed files)"
  scripts/check_no_em_dashes.sh "${SCAN_FILES[@]}"
fi

# 3. actionlint on changed workflows (conditional; <2s).
if [ ${#WF_FILES[@]} -gt 0 ]; then
  if command -v pre-commit >/dev/null 2>&1; then
    echo "==> [3/5] actionlint (changed workflows)"
    pre-commit run actionlint --files "${WF_FILES[@]}"
  fi
fi

# 4. terraform fmt -check + validate on changed modules (conditional; <5s).
if [ ${#TF_DIRS[@]} -gt 0 ]; then
  for d in "${TF_DIRS[@]}"; do
    echo "==> [4/5] terraform fmt: $d"
    terraform -chdir="$d" fmt -check -diff
  done
  # terraform validate needs an init to resolve providers; the unit-test
  # form `init -backend=false` is fast enough (<10s/module on warm cache).
  # Skip if init has never run (no .terraform dir) AND the operator hasn't
  # set CI_FAST_TF_VALIDATE=1; the server-side `Terraform plan on PR`
  # workflow is the canonical gate for full validate.
  if [ "${CI_FAST_TF_VALIDATE:-0}" = "1" ]; then
    for d in "${TF_DIRS[@]}"; do
      if [ -d "$d/.terraform" ]; then
        echo "==> [4/5] terraform validate: $d"
        terraform -chdir="$d" validate -no-color
      fi
    done
  fi
fi

# 5. ruff check on changed Python files (conditional; <10s for changed paths).
# Path-scoped: we pass the file list directly, not the whole repo. Each
# Python service ships its own ruff config under services/<svc>/pyproject.toml,
# but ruff's nearest-pyproject discovery handles that automatically when
# given a path inside the service tree.
if [ ${#PY_FILES[@]} -gt 0 ]; then
  if command -v ruff >/dev/null 2>&1; then
    echo "==> [5/5] ruff check (changed *.py)"
    ruff check "${PY_FILES[@]}"
  elif command -v uv >/dev/null 2>&1; then
    # Use the first changed file's service as the uv context so the
    # service's pinned ruff version applies. Fast path: most PRs touch
    # one service at a time.
    svc_dir=""
    for pf in "${PY_FILES[@]}"; do
      # walk up looking for pyproject.toml
      d=$(dirname "$pf")
      while [ "$d" != "." ] && [ "$d" != "/" ]; do
        if [ -f "$d/pyproject.toml" ]; then
          svc_dir="$d"
          break
        fi
        d=$(dirname "$d")
      done
      [ -n "$svc_dir" ] && break
    done
    if [ -n "$svc_dir" ]; then
      echo "==> [5/5] uv run ruff check (changed *.py via $svc_dir)"
      # Convert repo-relative paths to absolute so they resolve from svc_dir.
      abs_py=()
      for pf in "${PY_FILES[@]}"; do
        abs_py+=("$REPO_ROOT/$pf")
      done
      (cd "$svc_dir" && uv run ruff check "${abs_py[@]}")
    else
      echo "[ci-fast] WARN: ruff not on PATH and no service pyproject.toml found; skipping ruff."
    fi
  else
    echo "[ci-fast] WARN: neither ruff nor uv on PATH; skipping ruff. Server-side CI will catch lint."
  fi
fi

end_ts=$(date +%s)
elapsed=$(( end_ts - start_ts ))
echo ""
echo "==> ci-fast: all fast gates passed in ${elapsed}s"
if [ "$elapsed" -gt 90 ]; then
  echo "[ci-fast] WARN: ci-fast ran for ${elapsed}s (budget 90s); consider pushing slow phases server-side." >&2
fi

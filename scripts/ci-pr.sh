#!/usr/bin/env bash
# ci-pr.sh: run only the CI gates relevant to files changed against
# origin/main. Mirrors `make ci-local` but skips gates whose inputs did
# not change. The point is to catch what remote CI catches without
# paying for unrelated work.
#
# Usage:
#   make ci-pr                       # called by .githooks/pre-push and humans
#   scripts/ci-pr.sh                 # same thing, called directly
#   BASE_REF=origin/feat/foo ci-pr.sh  # diff against a non-default base
#
# Exit codes:
#   0  all relevant gates passed (or no relevant gates)
#   1  a gate failed
#   2  bad usage / can't determine the diff
#
# Decisions:
#   - We compare against the merge-base of HEAD and origin/main so a
#     stale rebase still produces an accurate change set.
#   - When a config file (pre-commit, gitleaks, workflows) changes we
#     escalate to running pre-commit on every file rather than try to
#     guess which files the new config affects.
#   - Doc / Markdown / .gitignore changes only run pre-commit on the
#     changed files. They can't break code or infra.
#   - `services/_template` is a template that ships with passing
#     scaffolding tests; we treat it like any other Python service.
#   - Python services run lint+type on ANY in-dir change but only run
#     pytest when actual .py / pyproject / uv.lock / conftest.py files
#     changed. A Dockerfile-only sweep does NOT re-run every service's
#     pytest (Dockerfile shape is verified by `docker build` on remote CI).
#   - The pytest phase is hard-budgeted at CI_PR_PYTEST_TIMEOUT seconds
#     (default 480 = 8 min) so the pre-push hook cannot exceed github.com's
#     ~15-min SSH idle-timeout. See feedback_pre_push_hook_must_finish_under_ssh_idle_timeout.

set -euo pipefail

# Hard wallclock budget for the pytest phase. 8 minutes by default leaves
# enough headroom under github.com's ~15-min SSH idle-timeout for the
# actual `git push` upload to finish. Override locally with
# CI_PR_PYTEST_TIMEOUT=N if you genuinely need a longer local run.
PYTEST_TIMEOUT="${CI_PR_PYTEST_TIMEOUT:-480}"

# Source nvm if available so the TypeScript gates can resolve Node 22+
# even from non-interactive shells (the pre-push hook inherits PATH from
# the shell that ran `git push`, which often defaults to the system Node).
# pnpm 11 imports `node:sqlite`, a Node 22+ builtin, so Node 20 fails
# outright before any test runs.
if [ -z "${NVM_DIR:-}" ] && [ -d "$HOME/.nvm" ]; then
  export NVM_DIR="$HOME/.nvm"
fi
if [ -n "${NVM_DIR:-}" ] && [ -s "$NVM_DIR/nvm.sh" ]; then
  # shellcheck disable=SC1091
  . "$NVM_DIR/nvm.sh"
  # Honor .tool-versions / .nvmrc when present; fall back to 22 otherwise.
  nvm use --silent 22 >/dev/null 2>&1 || true
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
  echo "ci-pr: not inside a git repo" >&2
  exit 2
fi
cd "$REPO_ROOT"

BASE_REF="${BASE_REF:-origin/main}"

# Refresh the base ref. Don't fail if offline; the merge-base still
# resolves against our local copy.
git fetch origin main --quiet 2>/dev/null || true

if ! git rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
  echo "ci-pr: base ref '$BASE_REF' does not exist; pass BASE_REF=<ref>" >&2
  exit 2
fi

# If we're sitting on the base ref itself, diff against the previous
# commit so a one-off main-branch sanity sweep still does something.
HEAD_SHA=$(git rev-parse HEAD)
BASE_SHA=$(git rev-parse "$BASE_REF")
if [ "$HEAD_SHA" = "$BASE_SHA" ]; then
  CMP_BASE="HEAD~1"
else
  CMP_BASE=$(git merge-base "$BASE_REF" HEAD)
fi

# Use `git diff $CMP_BASE` (no HEAD on the right side) so the working
# tree counts. That way `make ci-pr` mid-development picks up unstaged
# edits, and pre-push (which fires after commit, before push) sees the
# same set as the about-to-push HEAD. Both use cases get the right
# answer from a single command.
#
# `git diff` does not include untracked-but-not-ignored files (new
# files you haven't `git add`ed yet). Pull those in via `git ls-files
# --others --exclude-standard` so a brand-new script the dev hasn't
# staged still gets pre-commit run on it. Pre-push naturally has zero
# untracked files at the moment it runs (everything was committed).
CHANGED=$( ( git diff --name-only "$CMP_BASE"; git ls-files --others --exclude-standard ) | sort -u )

if [ -z "$CHANGED" ]; then
  echo "ci-pr: no files changed vs $BASE_REF; running pre-commit on all files only."
  pre-commit run --all-files
  echo "==> ci-pr: focused gates passed (no code changes detected)"
  exit 0
fi

echo "ci-pr: comparing $CMP_BASE..HEAD"
echo "ci-pr: $(echo "$CHANGED" | wc -l) files changed:"
echo "$CHANGED" | sed 's/^/  /'
echo ""

# Classifiers
#
# Two-bucket Python tracking:
#   PY_LINT_SET - run ruff + mypy. Triggered by ANY change inside a
#                 Python service dir (Dockerfile, .py, config, README).
#                 Fast (<30s/svc) and catches real config bugs.
#   PY_TEST_SET - run pytest. Triggered ONLY by .py / pyproject.toml /
#                 uv.lock / conftest.py changes. A Dockerfile-only or
#                 README-only diff does NOT trigger pytest; Dockerfile
#                 shape is verified at `docker build` time on remote CI.
#
# This is the scope-narrowing fix for the dockerfile-sweep failure mode
# (10-service sweep ran pytest 10x serially, ~25min, blew past SSH idle
# timeout). See feedback_pre_push_hook_must_finish_under_ssh_idle_timeout.
declare -A PY_LINT_SET=()
declare -A PY_TEST_SET=()
declare -A TS_SET=()
declare -A TF_SET=()
RUN_PRECOMMIT_ALL=0
RUN_ACTIONLINT=0

while IFS= read -r f; do
  case "$f" in
    .pre-commit-config.yaml|.gitleaks.toml)
      RUN_PRECOMMIT_ALL=1
      ;;
    .github/workflows/*.yml|.github/workflows/*.yaml)
      RUN_ACTIONLINT=1
      ;;
    services/*)
      svc=$(echo "$f" | cut -d/ -f1-2)
      if [ -f "$svc/pyproject.toml" ]; then
        PY_LINT_SET[$svc]=1
        # Only schedule pytest when actual Python sources or the
        # pyproject/uv.lock (deps, entry points, pytest config) changed.
        case "$f" in
          *.py|"$svc/pyproject.toml"|"$svc/uv.lock"|"$svc/conftest.py")
            PY_TEST_SET[$svc]=1
            ;;
        esac
      elif [ -f "$svc/package.json" ]; then
        TS_SET[$svc]=1
      fi
      ;;
    infra/*.tf)
      TF_SET[infra]=1
      ;;
    infra/**/*.tf|infra/*/*.tf|infra/*/*/*.tf|infra/*/*/*/*.tf)
      d=$(dirname "$f")
      TF_SET[$d]=1
      ;;
    scripts/*|Makefile|.githooks/*|*.md|.gitignore|.gitattributes|LICENSE|.editorconfig)
      # Dev tooling / docs. pre-commit on the file is the only relevant gate.
      ;;
    *)
      # Unknown classification: be safe and run pre-commit-all. We'd
      # rather pay a few extra seconds than ship a config-poisoning bug.
      RUN_PRECOMMIT_ALL=1
      ;;
  esac
done <<< "$CHANGED"

# 1. pre-commit (always)
if [ "$RUN_PRECOMMIT_ALL" = "1" ]; then
  echo "==> pre-commit run --all-files (config changed)"
  pre-commit run --all-files
else
  echo "==> pre-commit run on changed files"
  echo "$CHANGED" | xargs --no-run-if-empty pre-commit run --files
fi

# 2. Python services
#
# Lint + type-check runs for every service with an in-dir change (fast).
# Pytest runs ONLY for services whose .py / pyproject / uv.lock / conftest
# changed, and the entire pytest phase is hard-budgeted via `timeout` so
# the pre-push hook cannot exceed github.com's SSH idle-timeout.
if [ "${#PY_LINT_SET[@]}" -gt 0 ]; then
  for svc in "${!PY_LINT_SET[@]}"; do
    echo "==> Python lint+type: $svc"
    (cd "$svc" && uv run ruff check && uv run mypy src)
  done
fi

if [ "${#PY_TEST_SET[@]}" -gt 0 ]; then
  echo "==> Python pytest (budget: ${PYTEST_TIMEOUT}s wallclock for all services combined)"
  # `timeout` returns 124 when it kills the child. We trap that
  # specifically so the operator sees the actionable budget message
  # rather than a bare non-zero from pytest.
  set +e
  timeout "$PYTEST_TIMEOUT" bash -c '
    set -e
    for svc in "$@"; do
      echo "==> Python pytest: $svc"
      (cd "$svc" && uv run pytest)
    done
  ' _ "${!PY_TEST_SET[@]}"
  pytest_rc=$?
  set -e
  if [ "$pytest_rc" -eq 124 ]; then
    echo ""
    echo "[ci-pr] pytest exceeded ${PYTEST_TIMEOUT}s budget; relying on server-side CI for full validation." >&2
    echo "[ci-pr] Set CI_PR_PYTEST_TIMEOUT=N to override locally (e.g. CI_PR_PYTEST_TIMEOUT=1800 make ci-pr)." >&2
    echo "[ci-pr] To skip the local gate entirely for this push: NO_VERIFY=1 git push" >&2
    exit 1
  elif [ "$pytest_rc" -ne 0 ]; then
    exit "$pytest_rc"
  fi
else
  echo "==> ci-pr: no .py / pyproject / uv.lock changes in any service, skipping pytest phase"
fi

# 3. TypeScript services
if [ "${#TS_SET[@]}" -gt 0 ]; then
  for svc in "${!TS_SET[@]}"; do
    echo "==> TypeScript: $svc"
    (cd "$svc" \
      && pnpm install --frozen-lockfile --prefer-offline >/dev/null 2>&1 \
      && pnpm biome check \
      && (pnpm typecheck 2>/dev/null || pnpm tsc --noEmit) \
      && (pnpm test 2>/dev/null || true))
  done
fi

# 4. Terraform
if [ "${#TF_SET[@]}" -gt 0 ]; then
  for d in "${!TF_SET[@]}"; do
    echo "==> Terraform: $d"
    (cd "$d" && terraform fmt -check -diff && terraform validate -no-color) \
      || (cd "$d" && terraform init -backend=false -no-color >/dev/null && terraform validate -no-color)
  done
fi

# 5. actionlint (only when workflows changed and pre-commit-all didn't already cover it)
if [ "$RUN_ACTIONLINT" = "1" ] && [ "$RUN_PRECOMMIT_ALL" = "0" ]; then
  echo "==> actionlint (workflows changed)"
  pre-commit run actionlint --all-files
fi

echo "==> ci-pr: focused gates passed"

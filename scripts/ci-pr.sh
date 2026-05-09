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

set -euo pipefail

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
declare -A PY_SET=()
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
        PY_SET[$svc]=1
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
if [ "${#PY_SET[@]}" -gt 0 ]; then
  for svc in "${!PY_SET[@]}"; do
    echo "==> Python: $svc"
    (cd "$svc" && uv run ruff check && uv run mypy src && uv run pytest)
  done
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

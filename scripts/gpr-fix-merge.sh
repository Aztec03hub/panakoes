#!/usr/bin/env bash
# Usage: gpr-fix-merge <PR_NUMBER>
#
# scripts/gpr-fix-merge.sh: rebase a PR branch on top of main, force-push,
# and let auto-merge take it from there. Wraps the gh CLI dance Phil runs
# many times per day during the Panakoes solo-PR workflow.
#
# Failure modes handled:
#   1. `gh pr update-branch` is not a real subcommand on every gh version,
#      so we fall back to the REST API endpoint
#      `PUT /repos/{owner}/{repo}/pulls/{pull_number}/update-branch`.
#      That endpoint asks GitHub to rebase the PR branch on main server-side
#      and is the same operation that the green "Update branch" button
#      triggers in the web UI.
#   2. CHANGELOG.md and infra/README.md merge conflicts during a local
#      rebase are almost always additive (both branches added a bullet to
#      [Unreleased]). The classic union-merge sed trick removes the
#      conflict markers and keeps both sides; safe because both are
#      append-only docs.
#   3. Any other conflict path bails out with a clear error so the
#      developer can resolve it manually.
#
# Why a script and not a Makefile target alone:
# the merge dance has branching logic and stderr-aware fallbacks; a
# Makefile recipe would obscure the failure mode reporting.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $(basename "$0") <PR_NUMBER>" >&2
    exit 64
fi

PR_NUMBER="$1"

if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: gh CLI not found on PATH." >&2
    exit 1
fi

# Repo slug "<owner>/<repo>" derived from the current git remote.
REPO_SLUG="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
OWNER="${REPO_SLUG%%/*}"
REPO="${REPO_SLUG##*/}"

echo "[gpr] PR #${PR_NUMBER} on ${REPO_SLUG}"

# -----------------------------------------------------------------------------
# Step 1: ask GitHub to rebase the branch on main server-side.
# -----------------------------------------------------------------------------
echo "[gpr] requesting server-side branch update..."

if gh pr update-branch "${PR_NUMBER}" 2>/dev/null; then
    echo "[gpr] gh pr update-branch succeeded."
else
    # Fallback: gh pr update-branch is missing or rejected. Hit the REST
    # endpoint directly. 202 means GitHub accepted the rebase request.
    echo "[gpr] gh pr update-branch unavailable; falling back to REST API."
    if ! gh api -X PUT "repos/${OWNER}/${REPO}/pulls/${PR_NUMBER}/update-branch" >/dev/null 2>&1; then
        # Server-side rebase can fail when the merge has conflicts. In that
        # case we drop into a local rebase, resolve the known additive
        # conflict files automatically, and force-push.
        echo "[gpr] server-side rebase failed; attempting local rebase."

        BRANCH="$(gh pr view "${PR_NUMBER}" --json headRefName --jq '.headRefName')"
        echo "[gpr] checking out ${BRANCH}..."
        git fetch origin
        git checkout "${BRANCH}"
        git pull --ff-only origin "${BRANCH}" || true

        if git rebase origin/main; then
            echo "[gpr] local rebase clean."
        else
            # Inspect which files are conflicted. If the only conflicts are
            # the known additive docs, run the union sed trick and continue.
            CONFLICTS="$(git diff --name-only --diff-filter=U)"
            ALLOWED_RE='^(CHANGELOG\.md|infra/README\.md)$'
            UNEXPECTED="$(echo "${CONFLICTS}" | grep -Ev "${ALLOWED_RE}" || true)"
            if [ -n "${UNEXPECTED}" ]; then
                echo "ERROR: unresolvable conflict in:" >&2
                echo "${UNEXPECTED}" >&2
                echo "Resolve manually, then 'git rebase --continue' and re-run gpr-fix-merge." >&2
                exit 1
            fi
            for path in ${CONFLICTS}; do
                echo "[gpr] union-merging ${path}..."
                # Strip conflict markers; both sides are append-only doc
                # additions so concatenating both is the right answer.
                sed -i '/^<<<<<<<\|^=======\|^>>>>>>>/d' "${path}"
                git add "${path}"
            done
            git rebase --continue || {
                echo "ERROR: rebase --continue failed after union merge." >&2
                exit 1
            }
        fi
        git push --force-with-lease origin "${BRANCH}"
    fi
fi

# -----------------------------------------------------------------------------
# Step 2: enable auto-merge (squash + delete branch on success).
# -----------------------------------------------------------------------------
echo "[gpr] enabling auto-merge (squash, delete branch)..."
gh pr merge "${PR_NUMBER}" --squash --auto --delete-branch

echo "[gpr] done. CI will fire the merge once status checks pass."

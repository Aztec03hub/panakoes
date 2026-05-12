#!/usr/bin/env bash
# Usage: required-checks-add.sh "Check Name"
#
# scripts/required-checks-add.sh: add a workflow check name to the
# required-status-checks list of the branch protection ruleset for `main`.
#
# Background:
# Panakoes uses GitHub's newer rulesets (not the legacy branch protection
# rules) on the `main` branch. The "required status checks" list lives
# inside a `required_status_checks` rule on that ruleset. Updating it from
# the CLI is fiddly because the GraphQL/REST shape is a JSON document the
# whole ruleset gets PATCHed against.
#
# This script:
#   1. Locates the ruleset that targets `refs/heads/main` on the current repo.
#   2. Fetches its full JSON definition.
#   3. Appends `{"context": "<arg>"}` to the required_status_checks rule's
#      `parameters.required_status_checks` array, deduping on `context`.
#   4. PATCHes the modified ruleset back via gh api.
#
# Acceptance:
#   - Exit 0 on success.
#   - Exit non-zero with a clear error if the ruleset cannot be found,
#     the required_status_checks rule is missing, or jq is not installed.
#
# Why we gate on this:
# CLAUDE.md and the Panakoes lessons file both call out that required
# checks must be added IMMEDIATELY after a workflow's first run; otherwise
# broken-test PRs slip through. This script makes the operation a one-liner.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "Usage: $(basename "$0") \"Check Name\"" >&2
    exit 64
fi

CHECK_NAME="$1"

for tool in gh jq; do
    if ! command -v "${tool}" >/dev/null 2>&1; then
        echo "ERROR: ${tool} not found on PATH." >&2
        exit 1
    fi
done

REPO_SLUG="$(gh repo view --json nameWithOwner --jq '.nameWithOwner')"
echo "[required-checks-add] repo=${REPO_SLUG} check='${CHECK_NAME}'"

# -----------------------------------------------------------------------------
# Locate the ruleset that targets refs/heads/main.
# -----------------------------------------------------------------------------
RULESET_ID="$(
    gh api "repos/${REPO_SLUG}/rulesets" --paginate \
        | jq -r '
            .[] |
            select(
                (.target == "branch") and
                ((.conditions.ref_name.include // []) | index("refs/heads/main"))
            ) |
            .id
        ' \
        | head -n 1
)"

if [ -z "${RULESET_ID}" ]; then
    echo "ERROR: no ruleset found targeting refs/heads/main on ${REPO_SLUG}." >&2
    echo "Create the ruleset in the GitHub UI first, then re-run." >&2
    exit 1
fi

echo "[required-checks-add] ruleset id=${RULESET_ID}"

# -----------------------------------------------------------------------------
# Fetch, mutate, PATCH.
# -----------------------------------------------------------------------------
TMP="$(mktemp -t ruleset.XXXXXX.json)"
trap 'rm -f "${TMP}"' EXIT

gh api "repos/${REPO_SLUG}/rulesets/${RULESET_ID}" > "${TMP}"

# Confirm the required_status_checks rule exists. We do not synthesize one
# if missing; that is a deliberate setup choice the operator should make.
if ! jq -e '.rules[] | select(.type == "required_status_checks")' "${TMP}" >/dev/null; then
    echo "ERROR: ruleset ${RULESET_ID} has no required_status_checks rule." >&2
    echo "Add the rule via the GitHub UI first, then re-run." >&2
    exit 1
fi

# Idempotency: bail with a friendly message if the check is already listed.
if jq -e --arg c "${CHECK_NAME}" '
    .rules[]
    | select(.type == "required_status_checks")
    | .parameters.required_status_checks
    | map(.context) | index($c)
' "${TMP}" >/dev/null; then
    echo "[required-checks-add] '${CHECK_NAME}' already required; nothing to do."
    exit 0
fi

UPDATED="$(mktemp -t ruleset.updated.XXXXXX.json)"
trap 'rm -f "${TMP}" "${UPDATED}"' EXIT

jq --arg c "${CHECK_NAME}" '
    .rules |= map(
        if .type == "required_status_checks" then
            .parameters.required_status_checks += [{"context": $c, "integration_id": null}]
        else
            .
        end
    )
' "${TMP}" > "${UPDATED}"

# PATCH expects only the writable fields. Strip server-managed metadata.
PATCH_BODY="$(mktemp -t ruleset.patch.XXXXXX.json)"
trap 'rm -f "${TMP}" "${UPDATED}" "${PATCH_BODY}"' EXIT

jq '{
    name,
    target,
    enforcement,
    bypass_actors: (.bypass_actors // []),
    conditions,
    rules
}' "${UPDATED}" > "${PATCH_BODY}"

gh api -X PUT "repos/${REPO_SLUG}/rulesets/${RULESET_ID}" \
    --input "${PATCH_BODY}" >/dev/null

echo "[required-checks-add] '${CHECK_NAME}' added to required status checks for main."

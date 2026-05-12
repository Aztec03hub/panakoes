#!/usr/bin/env bash
# Companion to .github/codeql/queries/panakoes/iam-policy-resource-star.ql
#
# CodeQL has no first-class Terraform/HCL extractor in v4. This script
# performs the equivalent scan and emits a minimal SARIF document the
# CodeQL workflow can upload alongside native results.
#
# WHY shell instead of a real CodeQL pack: writing a custom extractor
# for HCL is a 3-day side project; a regex pass over `.tf` files catches
# the bug class today (resource = ["*"] is syntactically rare and easy
# to grep). Revisit if false-positive rate gets noisy.
#
# Allowlist: paths that are KNOWN-OK to have resources = ["*"].
# Add an entry here ONLY with a paired ADR or comment explaining why.

set -euo pipefail

ALLOWLIST_REGEX='^infra/dev/iam/main\.tf$'
OUT="${1:-iam-star.sarif}"

# Find any .tf line that assigns resources = ["*"] (with optional whitespace).
mapfile -t HITS < <(
  git ls-files '*.tf' | grep -vE '^\.github/codeql/test-fixtures/' | while read -r f; do
    if [[ "$f" =~ $ALLOWLIST_REGEX ]]; then continue; fi
    grep -nE '^[[:space:]]*resources[[:space:]]*=[[:space:]]*\[[[:space:]]*"\*"[[:space:]]*\]' "$f" 2>/dev/null \
      | awk -v file="$f" -F: '{print file ":" $1 ":" $0}' || true
  done
)

# Emit minimal SARIF 2.1.0
{
  # NOTE: the inner double quotes around `*` must be JSON-escaped as
  # `\\"` in printf so the emitted JSON contains `\"`. The previous
  # `\"` form was consumed by printf and produced bare `"` characters,
  # which broke JSON parsing on PR #280 first scan run (position 170).
  printf '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"panakoes-iam-star","rules":[{"id":"panakoes/iam-policy-resource-star","shortDescription":{"text":"IAM resources = [\\"*\\"] outside allowlist"}}]}},"results":['
  first=1
  for h in "${HITS[@]}"; do
    file="${h%%:*}"; rest="${h#*:}"; line="${rest%%:*}"
    [[ $first -eq 0 ]] && printf ','
    printf '{"ruleId":"panakoes/iam-policy-resource-star","level":"warning","message":{"text":"IAM resources = [\\"*\\"] outside allowlist"},"locations":[{"physicalLocation":{"artifactLocation":{"uri":"%s"},"region":{"startLine":%s}}}]}' "$file" "$line"
    first=0
  done
  printf ']}]}\n'
} > "$OUT"

echo "scan-iam-star: ${#HITS[@]} finding(s) -> $OUT" >&2
# Exit 0 always; this is report-only per PR scope.
exit 0

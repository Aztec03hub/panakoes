#!/usr/bin/env bash
# Companion to .github/codeql/queries/panakoes/secret-pattern-in-tf-default.ql
#
# Greps every committed `.tf` file for AWS / Stripe / Anthropic key
# patterns appearing inside a `default = "..."` line of a variable
# block. Emits SARIF for upload by the CodeQL workflow.
#
# Note: this overlaps gitleaks but covers the slow-drift case (a real
# value pasted into a default that gitleaks may have already accepted
# in a prior commit and that pre-commit no longer sees on subsequent
# diffs).

set -euo pipefail

OUT="${1:-tf-secrets.sarif}"

# Combined regex of key shapes
KEY_RE='(AKIA[0-9A-Z]{16}|sk_(live|test)_[0-9a-zA-Z]{24,}|sk-ant-[0-9a-zA-Z_-]{24,})'

mapfile -t HITS < <(
  git ls-files '*.tf' | grep -vE '^\.github/codeql/test-fixtures/' | while read -r f; do
    grep -nE "default[[:space:]]*=[[:space:]]*\"[^\"]*${KEY_RE}[^\"]*\"" "$f" 2>/dev/null \
      | awk -v file="$f" -F: '{print file ":" $1 ":" $0}' || true
  done
)

{
  printf '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"panakoes-tf-secrets","rules":[{"id":"panakoes/secret-pattern-in-tf-default","shortDescription":{"text":"Secret-shaped string in Terraform variable default"}}]}},"results":['
  first=1
  for h in "${HITS[@]}"; do
    file="${h%%:*}"; rest="${h#*:}"; line="${rest%%:*}"
    [[ $first -eq 0 ]] && printf ','
    printf '{"ruleId":"panakoes/secret-pattern-in-tf-default","level":"error","message":{"text":"Secret-shaped string in TF variable default"},"locations":[{"physicalLocation":{"artifactLocation":{"uri":"%s"},"region":{"startLine":%s}}}]}' "$file" "$line"
    first=0
  done
  printf ']}]}\n'
} > "$OUT"

echo "scan-tf-secrets: ${#HITS[@]} finding(s) -> $OUT" >&2
exit 0

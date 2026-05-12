#!/usr/bin/env bash
# Companion to .github/codeql/queries/panakoes/jwt-env-var-prefix-mismatch.ql
#
# Catches `AUTH_JWT_*` env-var reads in any file outside `services/auth/`.
# Covers both Python (`os.environ['AUTH_JWT_X']`, `os.getenv`) and
# TypeScript (`process.env.AUTH_JWT_X`). The CodeQL `.ql` query only
# handles Python; this script adds TS / JS coverage.

set -euo pipefail

OUT="${1:-jwt-prefix.sarif}"

# Patterns:
#   os.environ['AUTH_JWT_X'], os.environ["AUTH_JWT_X"], os.environ.get('AUTH_JWT_X')
#   os.getenv('AUTH_JWT_X')
#   process.env.AUTH_JWT_X, process.env['AUTH_JWT_X']
PAT='AUTH_JWT_[A-Z_]+'

mapfile -t HITS < <(
  git ls-files '*.py' '*.ts' '*.tsx' '*.js' '*.mjs' '*.svelte' \
    | grep -vE '^services/auth/' \
    | grep -vE '^\.github/codeql/test-fixtures/' \
    | while read -r f; do
        [[ -f "$f" ]] || continue
        grep -nE "${PAT}" "$f" 2>/dev/null \
          | awk -v file="$f" -F: '{print file ":" $1 ":" $0}' || true
      done
)

{
  printf '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"panakoes-jwt-prefix","rules":[{"id":"panakoes/jwt-env-var-prefix-mismatch","shortDescription":{"text":"AUTH_JWT_* env var read outside services/auth"}}]}},"results":['
  first=1
  for h in "${HITS[@]}"; do
    file="${h%%:*}"; rest="${h#*:}"; line="${rest%%:*}"
    [[ $first -eq 0 ]] && printf ','
    printf '{"ruleId":"panakoes/jwt-env-var-prefix-mismatch","level":"error","message":{"text":"AUTH_JWT_* env var read outside services/auth/. Validators must use JWT_*."},"locations":[{"physicalLocation":{"artifactLocation":{"uri":"%s"},"region":{"startLine":%s}}}]}' "$file" "$line"
    first=0
  done
  printf ']}]}\n'
} > "$OUT"

echo "scan-jwt-prefix: ${#HITS[@]} finding(s) -> $OUT" >&2
exit 0

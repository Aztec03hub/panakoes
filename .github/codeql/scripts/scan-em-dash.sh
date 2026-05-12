#!/usr/bin/env bash
# Companion to .github/codeql/queries/panakoes/em-dash-in-source.ql
#
# Catches em-dash (U+2014) and en-dash (U+2013) characters in any
# committed source file. The Python-only CodeQL query covers `.py`;
# this shell pass closes the gap for `.ts`, `.svelte`, `.tf`, `.md`,
# `.json`, etc.
#
# Excludes: CHANGELOG.md (historical), test fixtures, this script,
# and the query files themselves.

set -euo pipefail

OUT="${1:-em-dash.sarif}"

mapfile -t HITS < <(
  git ls-files \
    | grep -vE '^CHANGELOG\.md$' \
    | grep -vE '^\.github/codeql/test-fixtures/' \
    | grep -vE '^\.github/codeql/scripts/' \
    | grep -vE '^\.github/codeql/queries/' \
    | grep -vE '^\.github/codeql/README\.md$' \
    | while read -r f; do
        [[ -f "$f" ]] || continue
        # Only scan text files (skip binaries)
        if file "$f" | grep -q 'text'; then
          grep -nP '[\x{2013}\x{2014}]' "$f" 2>/dev/null \
            | awk -v file="$f" -F: '{print file ":" $1}' || true
        fi
      done
)

{
  printf '{"version":"2.1.0","runs":[{"tool":{"driver":{"name":"panakoes-em-dash","rules":[{"id":"panakoes/em-dash-in-source","shortDescription":{"text":"Em-dash or en-dash in source"}}]}},"results":['
  first=1
  for h in "${HITS[@]}"; do
    file="${h%:*}"; line="${h##*:}"
    [[ $first -eq 0 ]] && printf ','
    printf '{"ruleId":"panakoes/em-dash-in-source","level":"warning","message":{"text":"Em-dash or en-dash in source"},"locations":[{"physicalLocation":{"artifactLocation":{"uri":"%s"},"region":{"startLine":%s}}}]}' "$file" "$line"
    first=0
  done
  printf ']}]}\n'
} > "$OUT"

echo "scan-em-dash: ${#HITS[@]} finding(s) -> $OUT" >&2
exit 0

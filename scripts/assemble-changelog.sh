#!/usr/bin/env bash
# assemble-changelog.sh: collect .changelog/*.md fragments, group by
# category in Keep a Changelog order, and prepend a versioned block to
# CHANGELOG.md. Optionally prune the fragments after a successful
# write.
#
# Usage:
#   scripts/assemble-changelog.sh --version vX.Y.Z [--dry-run] [--prune]
#   scripts/assemble-changelog.sh --version vX.Y.Z --dry-run     # preview only
#   scripts/assemble-changelog.sh --version vX.Y.Z --prune       # write + delete fragments
#
# Flags:
#   --version VERSION   required unless --dry-run; the header will be "## [VERSION] - YYYY-MM-DD"
#   --dry-run           print the assembled block to stdout; never touch CHANGELOG.md or .changelog/
#   --prune             after successfully prepending, `git rm` each consumed fragment
#   --fragments-dir DIR override fragments directory (default: .changelog at repo root)
#   --changelog PATH    override CHANGELOG.md path (default: CHANGELOG.md at repo root)
#
# Exit codes:
#   0  success (or dry-run printed cleanly)
#   1  no fragments to assemble
#   2  bad usage / missing required flag
#   3  malformed fragment (missing or unknown category)
#
# Fragment format (the contract enforced here):
#   ---
#   category: Added | Changed | Deprecated | Removed | Fixed | Security
#   ---
#
#   - bullet 1
#   - bullet 2
#
# Categories appear in canonical Keep a Changelog order. Within a
# category, bullets are ordered by fragment filename (UTC-timestamp
# ascending) so the output is stable and reviewable.

set -euo pipefail

VERSION=""
DRY_RUN=false
PRUNE=false
FRAGMENTS_DIR=""
CHANGELOG_PATH=""

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

usage() {
  sed -n '2,30p' "$0"
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version)
      VERSION="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --prune)
      PRUNE=true
      shift
      ;;
    --fragments-dir)
      FRAGMENTS_DIR="${2:-}"
      shift 2
      ;;
    --changelog)
      CHANGELOG_PATH="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage
      ;;
  esac
done

FRAGMENTS_DIR="${FRAGMENTS_DIR:-$REPO_ROOT/.changelog}"
CHANGELOG_PATH="${CHANGELOG_PATH:-$REPO_ROOT/CHANGELOG.md}"

if [ -z "$VERSION" ] && [ "$DRY_RUN" = false ]; then
  echo "error: --version is required unless --dry-run is set" >&2
  exit 2
fi

if [ ! -d "$FRAGMENTS_DIR" ]; then
  echo "error: fragments dir not found: $FRAGMENTS_DIR" >&2
  exit 1
fi

# Collect fragment files, excluding the README. Sorted by filename so
# the UTC-timestamp prefix produces a stable order.
FRAGMENTS=()
while IFS= read -r -d '' f; do
  FRAGMENTS+=("$f")
done < <(find "$FRAGMENTS_DIR" -maxdepth 1 -type f -name '*.md' ! -name 'README.md' -print0 | LC_ALL=C sort -z)

if [ "${#FRAGMENTS[@]}" -eq 0 ]; then
  echo "error: no fragments to assemble in $FRAGMENTS_DIR" >&2
  exit 1
fi

# Canonical category order, per Keep a Changelog.
CATEGORIES=(Added Changed Deprecated Removed Fixed Security)

declare -A BUCKET
for c in "${CATEGORIES[@]}"; do
  BUCKET["$c"]=""
done

is_valid_category() {
  local c="$1"
  for known in "${CATEGORIES[@]}"; do
    if [ "$c" = "$known" ]; then
      return 0
    fi
  done
  return 1
}

# Parse each fragment: extract `category:` from the frontmatter, append
# the body (everything after the closing `---`) to that category's
# bucket. Tolerates trailing whitespace and either Unix or Windows line
# endings.
for f in "${FRAGMENTS[@]}"; do
  category=""
  in_frontmatter=false
  past_frontmatter=false
  body=""

  while IFS= read -r line || [ -n "$line" ]; do
    # Normalize CRLF.
    line="${line%$'\r'}"

    if [ "$past_frontmatter" = false ]; then
      if [ "$in_frontmatter" = false ]; then
        if [ "$line" = "---" ]; then
          in_frontmatter=true
          continue
        fi
        # Allow blank lines before the opening fence.
        if [ -z "$line" ]; then
          continue
        fi
        echo "error: fragment $f missing opening '---' frontmatter fence" >&2
        exit 3
      fi
      # In frontmatter.
      if [ "$line" = "---" ]; then
        past_frontmatter=true
        continue
      fi
      case "$line" in
        category:*)
          category="${line#category:}"
          # Strip leading/trailing whitespace.
          category="${category#"${category%%[![:space:]]*}"}"
          category="${category%"${category##*[![:space:]]}"}"
          ;;
        *)
          : # ignore other frontmatter keys; future-extensible
          ;;
      esac
    else
      body+="${line}"$'\n'
    fi
  done < "$f"

  if [ -z "$category" ]; then
    echo "error: fragment $f missing 'category:' in frontmatter" >&2
    exit 3
  fi

  if ! is_valid_category "$category"; then
    echo "error: fragment $f has unknown category '$category' (valid: ${CATEGORIES[*]})" >&2
    exit 3
  fi

  # Trim leading blank lines from the body so categories join cleanly.
  while [ "${body:0:1}" = $'\n' ]; do
    body="${body#$'\n'}"
  done
  # Trim a single trailing newline so we control spacing on join.
  body="${body%$'\n'}"

  if [ -n "${BUCKET[$category]}" ]; then
    BUCKET["$category"]+=$'\n'"$body"
  else
    BUCKET["$category"]="$body"
  fi
done

DATE_STAMP="$(date -u +%Y-%m-%d)"

HEADER_VERSION="${VERSION:-Unreleased}"

ASSEMBLED=""
ASSEMBLED+="## [${HEADER_VERSION}] - ${DATE_STAMP}"$'\n'

any_content=false
for c in "${CATEGORIES[@]}"; do
  content="${BUCKET[$c]}"
  if [ -n "$content" ]; then
    any_content=true
    ASSEMBLED+=$'\n'"### ${c}"$'\n'"${content}"$'\n'
  fi
done

if [ "$any_content" = false ]; then
  echo "error: fragments parsed but no categories had content" >&2
  exit 3
fi

if [ "$DRY_RUN" = true ]; then
  printf '%s' "$ASSEMBLED"
  exit 0
fi

if [ ! -f "$CHANGELOG_PATH" ]; then
  echo "error: CHANGELOG.md not found at $CHANGELOG_PATH" >&2
  exit 2
fi

# Splice the assembled block in immediately after the '## [Unreleased]'
# marker (and its accompanying stub note, if any). We keep the
# [Unreleased] header in place and insert the new versioned block right
# after the next blank line. If the marker is absent (older CHANGELOG
# shape), we fall back to prepending to the top of the file.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

awk -v block="$ASSEMBLED" '
  BEGIN { inserted = 0; in_unreleased = 0 }
  {
    if (!inserted && $0 ~ /^## \[Unreleased\]/) {
      in_unreleased = 1
      print
      next
    }
    if (!inserted && in_unreleased && $0 ~ /^## \[/) {
      # Hit the next versioned section; insert above it.
      printf "%s\n", block
      inserted = 1
      print
      next
    }
    print
  }
  END {
    if (!inserted) {
      # No subsequent versioned section was found; append the block.
      printf "%s\n", block
    }
  }
' "$CHANGELOG_PATH" > "$TMP"

mv "$TMP" "$CHANGELOG_PATH"
trap - EXIT

echo "wrote ${HEADER_VERSION} block to $CHANGELOG_PATH"

if [ "$PRUNE" = true ]; then
  for f in "${FRAGMENTS[@]}"; do
    rm -f "$f"
    echo "pruned $f"
  done
fi

exit 0

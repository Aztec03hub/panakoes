#!/usr/bin/env bash
# scripts/check_no_em_dashes.sh
#
# Pre-commit hook script that fails if any staged file contains an em-dash
# (U+2014) or en-dash (U+2013). Phil's hard rule: no em-dashes, ever.
#
# Behavior:
#   - Loops over file paths passed as positional arguments (pre-commit
#     `pass_filenames: true` provides the staged file list).
#   - Skips binary files (detected via `file --mime`).
#   - Skips lock files and known-vendored content (uv.lock, pnpm-lock.yaml,
#     .terraform.lock.hcl) and the .gitleaks.toml allowlist file.
#   - Reports any matches with file:line:content via grep -nF and exits 1.
#   - Exits 0 if all scanned files are clean.
#
# The forbidden characters are defined as octal escape sequences inside
# $'...' so this script itself stays clean of the very characters it forbids:
#   U+2014 EM DASH  -> UTF-8 0xE2 0x80 0x94 -> \342\200\224
#   U+2013 EN DASH  -> UTF-8 0xE2 0x80 0x93 -> \342\200\223

set -u

EM_DASH=$'\342\200\224'
EN_DASH=$'\342\200\223'

# Files we never scan: lock files and the gitleaks config allowlist.
should_skip_path() {
    local path="$1"
    case "$path" in
        *.lock) return 0 ;;
        *uv.lock) return 0 ;;
        *pnpm-lock.yaml) return 0 ;;
        *package-lock.json) return 0 ;;
        *yarn.lock) return 0 ;;
        *.terraform.lock.hcl) return 0 ;;
        *Cargo.lock) return 0 ;;
        *poetry.lock) return 0 ;;
        .gitleaks.toml) return 0 ;;
        */.gitleaks.toml) return 0 ;;
    esac
    return 1
}

# Detect binary files via libmagic. Anything not text/* is treated as binary.
is_binary() {
    local path="$1"
    local mime
    mime=$(file --brief --mime-type -- "$path" 2>/dev/null || echo "")
    case "$mime" in
        text/*|application/json|application/xml|application/javascript|application/x-shellscript|application/x-yaml|application/toml)
            return 1 ;;
        *)
            # Fall back to grep's binary heuristic if libmagic gives an unknown type.
            if grep -Iq . -- "$path" 2>/dev/null; then
                return 1
            fi
            return 0 ;;
    esac
}

failed=0

for path in "$@"; do
    # File may have been removed in this commit; skip silently.
    [ -f "$path" ] || continue

    if should_skip_path "$path"; then
        continue
    fi

    if is_binary "$path"; then
        continue
    fi

    # grep -nF: line-numbered, fixed-string match. Run once per forbidden char
    # so we can label the output clearly.
    if grep -nF -- "$EM_DASH" "$path" > /tmp/em_dash_hits.$$ 2>/dev/null; then
        if [ -s /tmp/em_dash_hits.$$ ]; then
            echo "ERROR: em-dash (U+2014) found in $path:" >&2
            sed "s|^|  $path:|" /tmp/em_dash_hits.$$ >&2
            failed=1
        fi
    fi
    if grep -nF -- "$EN_DASH" "$path" > /tmp/en_dash_hits.$$ 2>/dev/null; then
        if [ -s /tmp/en_dash_hits.$$ ]; then
            echo "ERROR: en-dash (U+2013) found in $path:" >&2
            sed "s|^|  $path:|" /tmp/en_dash_hits.$$ >&2
            failed=1
        fi
    fi
    rm -f /tmp/em_dash_hits.$$ /tmp/en_dash_hits.$$
done

if [ "$failed" -ne 0 ]; then
    echo "" >&2
    echo "Phil's hard rule: no em-dashes (U+2014) or en-dashes (U+2013), ever." >&2
    echo "Use commas, periods, parentheses, semicolons, or hyphens (-)." >&2
    exit 1
fi

exit 0

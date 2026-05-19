#!/usr/bin/env bash
# verify-agent-run.sh: mechanical post-completion verification for sub-agent dispatches.
#
# Why this exists:
#   After every sub-agent reports DONE, the orchestrator is supposed to run a
#   trust-but-verify checklist before integrating the work (read the run
#   report, confirm files_modified matches the diff, scan for em-dashes, run
#   gitleaks, check the progress log terminated cleanly, etc.). Doing this
#   ad-hoc per dispatch produces inconsistent results and lets discipline
#   gaps slip through. This script mechanizes the checklist so the
#   orchestrator runs the same verification every time.
#
# Reference: .agent-runs/README.md "When orchestrator MUST verify the report"
# Reference: CLAUDE.md "After a sub-agent returns, the orchestrator MUST..."
#
# Usage:
#   # After an agent finishes, the orchestrator runs:
#   ./scripts/verify-agent-run.sh --worktree ../panakoes-tier1-cuts
#
#   # Or with explicit paths:
#   ./scripts/verify-agent-run.sh \
#     --worktree ../panakoes-tier1-cuts \
#     --run-report ../panakoes-tier1-cuts/.agent-runs/2026-05-18T20-44-51Z-tier1-cost-cuts.md \
#     --progress-log ../panakoes-tier1-cuts/.agent-runs/2026-05-18T20-44-51Z-tier1-cost-cuts.progress.log \
#     --base origin/main
#
# Exit codes:
#   0   all checks passed
#   10  run report missing, malformed, or status != success
#   11  files_modified / files_created mismatch with git diff
#   12  em-dash detected in diff
#   13  gitleaks detected secret
#   14  progress log missing, gaps, or terminated non-cleanly
#   15  commit message does not follow Conventional Commits
#   16  run report missing Local-First Verification section
#   2   usage error (missing args, bad paths)

set -uo pipefail

# ---------- argument parsing ----------

WORKTREE=""
RUN_REPORT=""
PROGRESS_LOG=""
BASE_REF="origin/main"

usage() {
    cat <<'USAGE'
Usage: verify-agent-run.sh --worktree <path> [options]

Required:
  --worktree <path>          Path to the agent's worktree (the directory where it worked)

Optional:
  --run-report <path>        Explicit run report path. Default: newest *.md in <worktree>/.agent-runs/
  --progress-log <path>      Explicit progress log path. Default: matching *.progress.log
  --base <ref>               Base ref to diff against. Default: origin/main
  -h, --help                 Show this help

Exit codes:
  0   all checks passed
  10  run report missing, malformed, or status != success
  11  files_modified / files_created mismatch with git diff
  12  em-dash detected in diff
  13  gitleaks detected secret
  14  progress log missing, gaps, or terminated non-cleanly
  15  commit message does not follow Conventional Commits
  16  run report missing Local-First Verification section
  2   usage error
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --worktree)
            WORKTREE="${2:-}"
            shift 2 || { echo "error: --worktree needs a value" >&2; exit 2; }
            ;;
        --run-report)
            RUN_REPORT="${2:-}"
            shift 2 || { echo "error: --run-report needs a value" >&2; exit 2; }
            ;;
        --progress-log)
            PROGRESS_LOG="${2:-}"
            shift 2 || { echo "error: --progress-log needs a value" >&2; exit 2; }
            ;;
        --base)
            BASE_REF="${2:-}"
            shift 2 || { echo "error: --base needs a value" >&2; exit 2; }
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ -z "$WORKTREE" ]; then
    echo "error: --worktree is required" >&2
    usage >&2
    exit 2
fi

if [ ! -d "$WORKTREE" ]; then
    echo "error: worktree path does not exist: $WORKTREE" >&2
    exit 2
fi

WORKTREE=$(cd "$WORKTREE" && pwd)

# ---------- check tracking ----------

declare -a CHECK_RESULTS=()
OVERALL_RC=0

record() {
    # record <check-name> <PASS|FAIL> <message>
    local name="$1"
    local result="$2"
    local msg="${3:-}"
    if [ "$result" = "PASS" ]; then
        printf "  [PASS] %s\n" "$name"
        if [ -n "$msg" ]; then
            printf "         %s\n" "$msg"
        fi
        CHECK_RESULTS+=("PASS|$name|$msg")
    else
        printf "  [FAIL] %s\n" "$name"
        if [ -n "$msg" ]; then
            printf "         %s\n" "$msg"
        fi
        CHECK_RESULTS+=("FAIL|$name|$msg")
    fi
}

# ---------- discovery ----------

AGENT_RUNS_DIR="$WORKTREE/.agent-runs"

if [ -z "$RUN_REPORT" ]; then
    # Find newest *.md in .agent-runs/ that isn't README.md
    if [ -d "$AGENT_RUNS_DIR" ]; then
        RUN_REPORT=$(find "$AGENT_RUNS_DIR" -maxdepth 1 -name '*.md' ! -name 'README.md' -type f -printf '%T@ %p\n' 2>/dev/null \
            | sort -nr | head -n 1 | awk '{print $2}')
    fi
fi

if [ -z "$PROGRESS_LOG" ] && [ -n "$RUN_REPORT" ]; then
    # Replace .md extension with .progress.log
    candidate="${RUN_REPORT%.md}.progress.log"
    if [ -f "$candidate" ]; then
        PROGRESS_LOG="$candidate"
    fi
fi

if [ -z "$PROGRESS_LOG" ] && [ -d "$AGENT_RUNS_DIR" ]; then
    PROGRESS_LOG=$(find "$AGENT_RUNS_DIR" -maxdepth 1 -name '*.progress.log' -type f -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr | head -n 1 | awk '{print $2}')
fi

echo "verify-agent-run: starting checks"
echo "  worktree:     $WORKTREE"
echo "  base ref:     $BASE_REF"
echo "  run report:   ${RUN_REPORT:-<not found>}"
echo "  progress log: ${PROGRESS_LOG:-<not found>}"
echo ""

# ---------- check 1: run report exists and has valid YAML frontmatter ----------

echo "==> Check 1: run report exists with valid YAML frontmatter"
if [ -z "$RUN_REPORT" ] || [ ! -f "$RUN_REPORT" ]; then
    record "report-exists" "FAIL" "no run report found in $AGENT_RUNS_DIR"
    OVERALL_RC=10
else
    # Confirm starts with --- and has a closing ---
    first_line=$(head -n 1 "$RUN_REPORT" || true)
    if [ "$first_line" != "---" ]; then
        record "report-frontmatter" "FAIL" "run report does not start with YAML frontmatter delimiter '---': $RUN_REPORT"
        OVERALL_RC=10
    else
        # Use python3 + PyYAML to confirm the frontmatter parses cleanly
        py_check=$(python3 - "$RUN_REPORT" <<'PYEOF' 2>&1
import sys
try:
    import yaml
except ImportError:
    print("MISSING_YAML")
    sys.exit(0)
path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    text = f.read()
parts = text.split("---")
if len(parts) < 3:
    print("NO_CLOSING_DELIMITER")
    sys.exit(0)
front = parts[1]
try:
    data = yaml.safe_load(front)
except yaml.YAMLError as e:
    print(f"YAML_PARSE_ERROR: {e}")
    sys.exit(0)
if not isinstance(data, dict):
    print("FRONTMATTER_NOT_DICT")
    sys.exit(0)
status = data.get("status", "<missing>")
files_created = data.get("files_created") or []
files_modified = data.get("files_modified") or []
print(f"OK|status={status}|files_created={','.join(files_created)}|files_modified={','.join(files_modified)}")
PYEOF
)
        case "$py_check" in
            OK\|*)
                record "report-frontmatter" "PASS" "YAML parses cleanly"
                # extract status and file lists for downstream checks
                STATUS=$(echo "$py_check" | awk -F'|' '{for(i=1;i<=NF;i++) if ($i ~ /^status=/) {sub(/^status=/,"",$i); print $i}}')
                REPORT_CREATED=$(echo "$py_check" | awk -F'|' '{for(i=1;i<=NF;i++) if ($i ~ /^files_created=/) {sub(/^files_created=/,"",$i); print $i}}')
                REPORT_MODIFIED=$(echo "$py_check" | awk -F'|' '{for(i=1;i<=NF;i++) if ($i ~ /^files_modified=/) {sub(/^files_modified=/,"",$i); print $i}}')
                ;;
            MISSING_YAML)
                record "report-frontmatter" "FAIL" "PyYAML not installed; cannot parse frontmatter"
                OVERALL_RC=10
                ;;
            *)
                record "report-frontmatter" "FAIL" "$py_check"
                OVERALL_RC=10
                ;;
        esac
    fi
fi

# ---------- check 2: status == success ----------

echo ""
echo "==> Check 2: run report status is 'success'"
if [ -n "${STATUS:-}" ]; then
    if [ "$STATUS" = "success" ]; then
        record "report-status" "PASS" "status=success"
    else
        record "report-status" "FAIL" "status=$STATUS (expected 'success'). Halting downstream checks since report self-reports failure."
        OVERALL_RC=10
        # Print summary and exit early per the spec
        echo ""
        echo "================================================================"
        echo "OVERALL: FAIL (status from run report itself is not 'success')"
        echo "================================================================"
        exit "$OVERALL_RC"
    fi
else
    record "report-status" "FAIL" "could not extract status from frontmatter"
    OVERALL_RC=10
fi

# ---------- check 3: files_created + files_modified match git diff ----------

echo ""
echo "==> Check 3: report file lists match git diff vs $BASE_REF"

# Get the diff file list from git
DIFF_FILES=""
if git -C "$WORKTREE" rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    DIFF_FILES=$(git -C "$WORKTREE" diff --name-only "$BASE_REF"...HEAD 2>/dev/null | sort -u)
else
    record "diff-file-list" "FAIL" "base ref '$BASE_REF' does not resolve in worktree"
    if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=11; fi
    DIFF_FILES=""
fi

if [ -n "${REPORT_CREATED:-}" ] || [ -n "${REPORT_MODIFIED:-}" ]; then
    # Build a sorted unique set of files claimed in the report.
    # Strip .agent-runs/ paths: the agent's own run report + progress log live
    # there but are gitignored by design, so they never appear in `git diff`.
    # Listing them in files_created is correct from the agent's perspective;
    # checking them against git diff is a category error.
    REPORT_FILES=$( (printf '%s\n' "${REPORT_CREATED:-}" "${REPORT_MODIFIED:-}" \
        | tr ',' '\n' | sed '/^$/d' | grep -v '^\.agent-runs/' | sort -u) || true)

    # Tolerate .changelog/ adds being absent from the report
    TOLERATED_EXTRAS=$(printf '%s\n' "$DIFF_FILES" | grep -E '^\.changelog/' || true)

    # diff_only = files in DIFF_FILES but NOT in REPORT_FILES (excluding tolerated extras)
    DIFF_ONLY=$(comm -23 <(printf '%s\n' "$DIFF_FILES" | sed '/^$/d') <(printf '%s\n' "$REPORT_FILES" | sed '/^$/d') || true)
    # subtract tolerated extras
    if [ -n "$TOLERATED_EXTRAS" ]; then
        DIFF_ONLY=$(comm -23 <(printf '%s\n' "$DIFF_ONLY") <(printf '%s\n' "$TOLERATED_EXTRAS") || true)
    fi

    # report_only = files claimed in REPORT_FILES but NOT in DIFF_FILES
    REPORT_ONLY=$(comm -13 <(printf '%s\n' "$DIFF_FILES" | sed '/^$/d') <(printf '%s\n' "$REPORT_FILES" | sed '/^$/d') || true)

    # Strip empties
    DIFF_ONLY=$(printf '%s' "$DIFF_ONLY" | sed '/^$/d')
    REPORT_ONLY=$(printf '%s' "$REPORT_ONLY" | sed '/^$/d')

    if [ -z "$DIFF_ONLY" ] && [ -z "$REPORT_ONLY" ]; then
        record "files-match" "PASS" "report file lists match git diff"
    else
        delta_msg=""
        if [ -n "$DIFF_ONLY" ]; then
            delta_msg+="files in diff but not in report:"
            while IFS= read -r line; do
                [ -n "$line" ] && delta_msg+=$'\n           '"$line"
            done <<< "$DIFF_ONLY"
        fi
        if [ -n "$REPORT_ONLY" ]; then
            [ -n "$delta_msg" ] && delta_msg+=$'\n         '
            delta_msg+="files in report but not in diff:"
            while IFS= read -r line; do
                [ -n "$line" ] && delta_msg+=$'\n           '"$line"
            done <<< "$REPORT_ONLY"
        fi
        record "files-match" "FAIL" "$delta_msg"
        if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=11; fi
    fi
else
    if [ -n "$DIFF_FILES" ]; then
        record "files-match" "FAIL" "report declared no files but git diff shows changes"
        if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=11; fi
    else
        record "files-match" "PASS" "no files in report or diff (no-op branch)"
    fi
fi

# ---------- check 4: em-dash scan of the diff ----------

echo ""
echo "==> Check 4: em-dash / en-dash scan of diff vs $BASE_REF"

# U+2014 = EM DASH, U+2013 = EN DASH. Use literal bytes in the regex.
EM_DASH_BYTES=$'\xe2\x80\x94'
EN_DASH_BYTES=$'\xe2\x80\x93'

EM_HITS=""
if git -C "$WORKTREE" rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    # Only check lines added in the diff (lines starting with '+' but not '+++')
    EM_HITS=$(git -C "$WORKTREE" diff "$BASE_REF"...HEAD 2>/dev/null \
        | grep -n -e "$EM_DASH_BYTES" -e "$EN_DASH_BYTES" \
        | grep -v '^[0-9]*:+++' \
        | grep '^[0-9]*:+' || true)
fi

if [ -n "$EM_HITS" ]; then
    hit_count=$(printf '%s\n' "$EM_HITS" | wc -l)
    sample=$(printf '%s\n' "$EM_HITS" | head -n 3)
    record "no-em-dashes" "FAIL" "found $hit_count line(s) with em-dash or en-dash; sample:"$'\n           '"$(printf '%s\n' "$sample" | sed ':a;N;$!ba;s/\n/\n           /g')"
    if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=12; fi
else
    record "no-em-dashes" "PASS" "no em-dashes or en-dashes in diff"
fi

# ---------- check 5: gitleaks scan ----------

echo ""
echo "==> Check 5: gitleaks scan of working tree"

if ! command -v gitleaks >/dev/null 2>&1; then
    record "gitleaks" "PASS" "WARN: gitleaks not on PATH; skipping (pre-push hook also runs gitleaks)"
else
    # Use --no-git so it scans the working tree files directly, --no-banner for clean output.
    # Honor the worktree's local .gitleaks.toml allowlist if present.
    gl_config_arg=()
    if [ -f "$WORKTREE/.gitleaks.toml" ]; then
        gl_config_arg=(--config "$WORKTREE/.gitleaks.toml")
    fi
    if gitleaks detect --source "$WORKTREE" --no-git --no-banner --redact "${gl_config_arg[@]}" >/tmp/verify-agent-gitleaks.log 2>&1; then
        record "gitleaks" "PASS" "no leaks detected"
    else
        rc=$?
        if [ "$rc" -eq 1 ]; then
            # Exit code 1 = leaks found
            sample=$(head -n 20 /tmp/verify-agent-gitleaks.log)
            record "gitleaks" "FAIL" "gitleaks detected secret(s); sample:"$'\n           '"$(printf '%s\n' "$sample" | sed ':a;N;$!ba;s/\n/\n           /g')"
            if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=13; fi
        else
            record "gitleaks" "FAIL" "gitleaks exited with rc=$rc (not a clean scan); see /tmp/verify-agent-gitleaks.log"
            if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=13; fi
        fi
    fi
fi

# ---------- check 6: progress log clean termination ----------

echo ""
echo "==> Check 6: progress log shows clean termination"

if [ -z "$PROGRESS_LOG" ] || [ ! -f "$PROGRESS_LOG" ]; then
    record "progress-log" "FAIL" "no progress log found (expected $AGENT_RUNS_DIR/*.progress.log)"
    if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=14; fi
else
    last_line=$(grep -v '^$' "$PROGRESS_LOG" | tail -n 1 || true)
    if [ -z "$last_line" ]; then
        record "progress-log" "FAIL" "progress log is empty: $PROGRESS_LOG"
        if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=14; fi
    else
        case "$last_line" in
            *"[BLOCKED"*|*"BLOCKED "*)
                record "progress-log" "FAIL" "last line is BLOCKED: $last_line"
                if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=14; fi
                ;;
            *"[ESCALATING"*|*"ESCALATING "*)
                record "progress-log" "FAIL" "last line is ESCALATING: $last_line"
                if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=14; fi
                ;;
            *"[DONE]"*)
                if printf '%s' "$last_line" | grep -q 'status=success'; then
                    record "progress-log" "PASS" "clean DONE with status=success"
                elif printf '%s' "$last_line" | grep -q 'status=failure'; then
                    # Clean failure terminal: agent stopped legitimately (BLOCKED/ESCALATED earlier)
                    # and surfaced. Not a verify-script error; orchestrator needs to decide.
                    record "progress-log" "FAIL" "DONE status=failure: agent surfaced cleanly; orchestrator decision needed: $last_line"
                    if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=14; fi
                else
                    record "progress-log" "FAIL" "last line is [DONE] but missing status=success|failure: $last_line"
                    if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=14; fi
                fi
                ;;
            *)
                record "progress-log" "FAIL" "last line is not [DONE]: $last_line"
                if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=14; fi
                ;;
        esac
    fi
fi

# ---------- check 7: conventional commits format ----------

echo ""
echo "==> Check 7: Conventional Commits format on commits since $BASE_REF"

CC_RE='^(feat|fix|chore|docs|refactor|test|style|ci|perf|build|security)(\([a-z0-9._/-]+\))?!?: .+'
bad_commits=""

if git -C "$WORKTREE" rev-parse --verify "$BASE_REF" >/dev/null 2>&1; then
    # Read commit subjects since base
    while IFS=$'\t' read -r sha subject; do
        [ -z "$sha" ] && continue
        if ! printf '%s' "$subject" | grep -Pq "$CC_RE"; then
            bad_commits+="$sha $subject"$'\n'
        fi
    done < <(git -C "$WORKTREE" log --format='%h%x09%s' "$BASE_REF..HEAD" 2>/dev/null)
fi

if [ -n "$bad_commits" ]; then
    record "conv-commits" "FAIL" "non-conforming commit subjects:"$'\n           '"$(printf '%s' "$bad_commits" | sed '/^$/d' | sed ':a;N;$!ba;s/\n/\n           /g')"
    if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=15; fi
else
    cnt=$(git -C "$WORKTREE" log --format='%h' "$BASE_REF..HEAD" 2>/dev/null | wc -l)
    record "conv-commits" "PASS" "$cnt commit(s) conform to Conventional Commits"
fi

# ---------- check 8: run report has Local-First Verification section ----------

echo ""
echo "==> Check 8: run report contains Local-First Verification section"

if [ -n "$RUN_REPORT" ] && [ -f "$RUN_REPORT" ]; then
    if grep -Eqi '^#{1,6}\s+Local[- ]First Verification' "$RUN_REPORT"; then
        # Confirm the section has at least one non-empty line of content before the next heading
        section_body=$(awk '
            /^#{1,6}[[:space:]]+[Ll]ocal[- ][Ff]irst [Vv]erification/ { found=1; next }
            found && /^#{1,6}[[:space:]]+/ { exit }
            found { print }
        ' "$RUN_REPORT" | sed '/^$/d')
        if [ -n "$section_body" ]; then
            line_count=$(printf '%s\n' "$section_body" | wc -l)
            record "local-first-verif" "PASS" "section present with $line_count non-empty lines"
        else
            record "local-first-verif" "FAIL" "Local-First Verification section header is present but body is empty"
            if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=16; fi
        fi
    else
        record "local-first-verif" "FAIL" "run report does not contain a 'Local-First Verification' section heading"
        if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=16; fi
    fi
else
    record "local-first-verif" "FAIL" "no run report to check"
    if [ "$OVERALL_RC" -eq 0 ]; then OVERALL_RC=16; fi
fi

# ---------- summary ----------

echo ""
echo "================================================================"
pass_count=0
fail_count=0
for r in "${CHECK_RESULTS[@]}"; do
    case "$r" in
        PASS\|*) pass_count=$((pass_count + 1)) ;;
        FAIL\|*) fail_count=$((fail_count + 1)) ;;
    esac
done
total=$((pass_count + fail_count))
if [ "$OVERALL_RC" -eq 0 ]; then
    echo "OVERALL: PASS ($pass_count/$total checks passed)"
else
    echo "OVERALL: FAIL ($fail_count/$total checks failed; exit code $OVERALL_RC)"
fi
echo "================================================================"

exit "$OVERALL_RC"

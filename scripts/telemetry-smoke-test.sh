#!/usr/bin/env bash
#
# telemetry-smoke-test.sh -- verify the tool-trace telemetry stack end-to-end.
#
# Run this in a FRESH `claude` session (not the one that shipped the
# implementation) so the .claude/settings.json hook registrations
# actually fire. Designed to be the first-validation tool after PR #414
# (telemetry implementation) and PR #415 (reconciliations) merged.
#
# What it does:
#   1. Confirms the hook shim and flusher exist.
#   2. Counts spool files (proves hooks have fired).
#   3. Runs the flusher once to drain spool -> SQLite.
#   4. Asks SQLite for event count + recent events.
#   5. Reports a green/red verdict.
#
# Usage:
#   scripts/telemetry-smoke-test.sh            # one-shot validation
#   scripts/telemetry-smoke-test.sh --verbose  # show event-by-event detail
#
# The script is read-only against the spool and SQLite. It runs the
# flusher in --once mode so it doesn't start a long-running daemon.

set -euo pipefail

VERBOSE=0
if [ "${1:-}" = "--verbose" ] || [ "${1:-}" = "-v" ]; then
    VERBOSE=1
fi

TELEMETRY_DIR="${PANAKOES_TELEMETRY_DIR:-${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/panakoes-telemetry}"
SPOOL="$TELEMETRY_DIR/spool"
SQLITE="$TELEMETRY_DIR/telemetry.sqlite"

REPO_ROOT="$(git rev-parse --show-toplevel)"
SHIM="$REPO_ROOT/.claude/hooks/trace-shim.sh"
FLUSHER="$REPO_ROOT/scripts/telemetry-flusher.py"
STATUS="$REPO_ROOT/scripts/telemetry-status.sh"
SETTINGS="$REPO_ROOT/.claude/settings.json"

ok=0
fail=0
check() {
    local name="$1"
    local result="$2"
    local detail="${3:-}"
    if [ "$result" = "PASS" ]; then
        printf "  [PASS] %s%s\n" "$name" "${detail:+ -- $detail}"
        ok=$((ok+1))
    else
        printf "  [FAIL] %s%s\n" "$name" "${detail:+ -- $detail}"
        fail=$((fail+1))
    fi
}

echo "telemetry-smoke-test: starting"
echo "  telemetry dir: $TELEMETRY_DIR"
echo "  spool:         $SPOOL"
echo "  sqlite:        $SQLITE"
echo ""

# 1. Static artifacts exist
echo "==> Check 1: shipped artifacts present"
[ -x "$SHIM" ] && check "shim-executable" PASS "$SHIM" || check "shim-executable" FAIL "$SHIM (run chmod +x?)"
[ -f "$FLUSHER" ] && check "flusher-present" PASS "$FLUSHER" || check "flusher-present" FAIL
[ -x "$STATUS" ] && check "status-script" PASS "$STATUS" || check "status-script" FAIL
[ -f "$SETTINGS" ] && check "settings-json" PASS "$SETTINGS" || check "settings-json" FAIL

# 2. Settings registers the 12 hook events
echo ""
echo "==> Check 2: .claude/settings.json registers all 12 hook events"
expected_events="SessionStart SessionEnd UserPromptSubmit Stop PreToolUse PostToolUse PostToolUseFailure SubagentStart SubagentStop PreCompact Notification PermissionRequest"
missing=""
for ev in $expected_events; do
    if ! grep -q "\"$ev\"" "$SETTINGS" 2>/dev/null; then
        missing="$missing $ev"
    fi
done
[ -z "$missing" ] && check "hook-registrations" PASS "all 12 events present" || check "hook-registrations" FAIL "missing:$missing"

# 3. Spool exists and has files
echo ""
echo "==> Check 3: spool dir has event files (proves hooks fired)"
if [ -d "$SPOOL" ]; then
    nfiles=$(find "$SPOOL" -maxdepth 3 -type f -name '*.json' 2>/dev/null | wc -l)
    if [ "$nfiles" -gt 0 ]; then
        check "spool-has-files" PASS "$nfiles event file(s)"
        if [ "$VERBOSE" -eq 1 ]; then
            echo "    sample (first 3):"
            find "$SPOOL" -maxdepth 3 -type f -name '*.json' | head -3 | sed 's|^|      |'
        fi
    else
        check "spool-has-files" FAIL "$SPOOL exists but is empty. Hooks have not fired. Start a fresh \`claude\` session and run any tool, then re-run this script."
    fi
else
    check "spool-has-files" FAIL "$SPOOL does not exist. Start a fresh \`claude\` session in this repo to trigger SessionStart hook, then re-run."
fi

# 4. Run flusher once
echo ""
echo "==> Check 4: flusher drains spool -> SQLite"
if [ -f "$FLUSHER" ]; then
    if out=$(python3 "$FLUSHER" --once 2>&1); then
        check "flusher-runs" PASS "exit 0"
        [ "$VERBOSE" -eq 1 ] && printf "    output:\n%s\n" "$out" | sed 's|^|      |' | head -20
    else
        check "flusher-runs" FAIL "exit non-zero"
        printf "    last 5 lines:\n%s\n" "$(echo "$out" | tail -5)" | sed 's|^|      |'
    fi
else
    check "flusher-runs" FAIL "flusher missing"
fi

# 5. SQLite has events
echo ""
echo "==> Check 5: SQLite has captured events"
if [ -f "$SQLITE" ]; then
    if command -v sqlite3 >/dev/null 2>&1; then
        count=$(sqlite3 "$SQLITE" 'SELECT COUNT(*) FROM events;' 2>/dev/null || echo "ERR")
        case "$count" in
            ''|*[!0-9]*)
                check "sqlite-events" FAIL "could not query events table: $count"
                ;;
            0)
                check "sqlite-events" FAIL "events table is empty. Flusher ran but found nothing to drain."
                ;;
            *)
                check "sqlite-events" PASS "$count event(s) captured"
                if [ "$VERBOSE" -eq 1 ]; then
                    echo "    latest 5 events:"
                    sqlite3 "$SQLITE" "SELECT printf('%-20s | %-24s | %s', hook_event_name, timestamp, COALESCE(tool_name, '-')) FROM events ORDER BY id DESC LIMIT 5;" 2>/dev/null | sed 's|^|      |'
                fi
                ;;
        esac
    else
        check "sqlite-events" FAIL "sqlite3 CLI not installed; sudo apt install sqlite3"
    fi
else
    check "sqlite-events" FAIL "$SQLITE does not exist. Flusher should create it on first run; investigate flusher errors above."
fi

# 6. Status script runs cleanly
echo ""
echo "==> Check 6: telemetry-status.sh runs"
if [ -x "$STATUS" ]; then
    if "$STATUS" >/dev/null 2>&1; then
        check "status-runs" PASS
    else
        check "status-runs" FAIL "exit non-zero (re-run with --verbose to see)"
    fi
else
    check "status-runs" FAIL "status script not executable"
fi

# Verdict
echo ""
echo "================================================================"
if [ "$fail" -eq 0 ]; then
    echo "OVERALL: PASS ($ok/$((ok+fail)) checks passed)"
    echo ""
    echo "Telemetry stack is live and capturing events. Next steps:"
    echo "  - Start the flusher as a daemon: python3 $FLUSHER &"
    echo "  - View status periodically: $STATUS"
    echo "  - Query SQLite directly: sqlite3 $SQLITE 'SELECT * FROM events LIMIT 10'"
    exit 0
else
    echo "OVERALL: FAIL ($fail/$((ok+fail)) checks failed; see above)"
    echo ""
    echo "Most-likely cause if spool is empty: you ran this script in the SAME"
    echo "claude session that shipped the implementation. Hooks only register"
    echo "on session start, so they're not active in this session. Close + reopen"
    echo "a fresh \`claude\` session in this repo and re-run."
    exit 1
fi

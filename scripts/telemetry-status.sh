#!/usr/bin/env bash
# telemetry-status.sh: one-shot status report for the panakoes telemetry stack.
#
# Surfaces: spool depth, disler reachability (via GET /events/recent per HIGH-06),
# flusher PID + last activity, latest 10 events.
#
# Exit code: 0 always (status, not a gate). Use `--strict` to exit non-zero if
# anything is unhealthy.
#
# Design references: docs/design/tool-trace-telemetry.md Section 4.4 (failure
# handling), 4.5 (operational setup), 6.1 (storage paths).

set -uo pipefail

LOGDIR="${PANAKOES_TELEMETRY_DIR:-${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/panakoes-telemetry}"
SPOOL="$LOGDIR/spool"
DB="$LOGDIR/telemetry.sqlite"
FLUSHER_LOG="$LOGDIR/flusher.log"
DISLER_URL="${DISLER_URL:-http://localhost:4000}"
DISLER_ENABLED="${DISLER_ENABLED:-false}"
DISLER_HEALTH_PATH="${DISLER_HEALTH_PATH:-/events/recent}"

STRICT=0
if [ "${1:-}" = "--strict" ]; then
  STRICT=1
fi

unhealthy=0
print_section() { printf '\n== %s ==\n' "$1"; }

print_section "panakoes telemetry status"
printf 'state dir:   %s\n' "$LOGDIR"
printf 'disler url:  %s (enabled=%s)\n' "$DISLER_URL" "$DISLER_ENABLED"

print_section "spool"
if [ -d "$SPOOL" ]; then
  depth=$(find "$SPOOL" -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
  printf 'spool depth: %s file(s)\n' "$depth"
  if [ "${depth:-0}" -ge 10000 ]; then
    printf 'STATUS:      CRITICAL (>=10000 files; flusher hard-stop threshold)\n'
    unhealthy=1
  elif [ "${depth:-0}" -ge 1000 ]; then
    printf 'STATUS:      WARN (>=1000 files; flusher should be draining faster)\n'
    unhealthy=1
  else
    printf 'STATUS:      OK\n'
  fi
  # Per-session breakdown for context
  if [ "${depth:-0}" -gt 0 ]; then
    printf 'sessions with pending files:\n'
    find "$SPOOL" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null \
      | while read -r sid; do
          n=$(find "$SPOOL/$sid" -type f -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
          [ "${n:-0}" -gt 0 ] && printf '  %s: %s\n' "$sid" "$n"
        done
  fi
else
  printf 'spool dir does not exist; flusher has never run\n'
fi

print_section "sqlite"
if [ -f "$DB" ]; then
  size=$(du -h "$DB" 2>/dev/null | awk '{print $1}')
  printf 'database:    %s (%s)\n' "$DB" "$size"
  if command -v sqlite3 >/dev/null 2>&1; then
    nrows=$(sqlite3 "$DB" 'SELECT COUNT(*) FROM events' 2>/dev/null || echo "?")
    printf 'total rows:  %s\n' "$nrows"
    last_ts=$(sqlite3 "$DB" 'SELECT MAX(timestamp) FROM events' 2>/dev/null || echo "?")
    printf 'last event:  %s\n' "${last_ts:-(none)}"
    unposted=$(sqlite3 "$DB" 'SELECT COUNT(*) FROM events WHERE disler_pushed_at IS NULL' 2>/dev/null || echo "?")
    printf 'unposted:    %s (rows pending disler POST)\n' "$unposted"
  else
    printf 'sqlite3 binary not on PATH; cannot inspect\n'
  fi
else
  printf 'database does not exist yet\n'
fi

print_section "flusher"
pids=$(pgrep -f 'telemetry-flusher.py' 2>/dev/null || true)
if [ -n "$pids" ]; then
  for pid in $pids; do
    started=$(ps -o lstart= -p "$pid" 2>/dev/null || echo "?")
    printf 'flusher PID: %s (started: %s)\n' "$pid" "$started"
  done
else
  printf 'flusher PID: (none running)\n'
  unhealthy=1
fi
if [ -f "$FLUSHER_LOG" ]; then
  printf 'last 5 log lines:\n'
  tail -n 5 "$FLUSHER_LOG" 2>/dev/null | sed 's/^/  /'
else
  printf 'flusher log:  (not yet written)\n'
fi

print_section "disler reachability"
if [ "$DISLER_ENABLED" = "true" ] || [ "$DISLER_ENABLED" = "1" ]; then
  url="${DISLER_URL%/}${DISLER_HEALTH_PATH}"
  if command -v curl >/dev/null 2>&1; then
    # GET /events/recent per HIGH-06 (orchestrator-verified: disler has no /health)
    http_code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "$url" 2>/dev/null || echo "000")
    printf 'GET %s -> HTTP %s\n' "$url" "$http_code"
    case "$http_code" in
      2*|3*|405) printf 'STATUS:      OK (reachable)\n' ;;
      000)       printf 'STATUS:      UNREACHABLE (network error or timeout)\n'; unhealthy=1 ;;
      *)         printf 'STATUS:      DEGRADED (HTTP %s)\n' "$http_code"; unhealthy=1 ;;
    esac
  else
    printf 'curl not on PATH; cannot probe\n'
    unhealthy=1
  fi
else
  printf 'DISLER_ENABLED=false; skipping probe\n'
fi

print_section "latest 10 events"
if [ -f "$DB" ] && command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 -header -column "$DB" \
    'SELECT timestamp, hook_event_name, tool_name, substr(brief,1,60) AS brief
     FROM events ORDER BY id DESC LIMIT 10;' 2>/dev/null \
    | sed 's/^/  /' || printf '  (query failed)\n'
else
  printf '  (no database; nothing to show)\n'
fi

echo ""
if [ "$STRICT" = "1" ] && [ "$unhealthy" = "1" ]; then
  exit 1
fi
exit 0

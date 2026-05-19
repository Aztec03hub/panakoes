#!/usr/bin/env bash
# trace-shim.sh: async hook intake for the panakoes tool-trace telemetry stack.
#
# Reads a Claude Code hook event JSON from stdin, writes it verbatim to a
# unique file under the spool directory, returns within milliseconds. The
# heavy work (gitleaks redaction, brief extraction, trace-context plumbing,
# SQLite insert, disler POST) happens out-of-band in scripts/telemetry-flusher.py.
#
# Performance budget: 15 ms p99 warm per design Section 7 (ADV-HIGH-04). The
# original 5 ms target was unmeetable; the jq collapse below brings p99 inside
# the relaxed ceiling.
#
# Design references:
#   - Section 3.5 (async shim hooks)
#   - Section 3.7 (one-file-per-event atomicity via mktemp O_CREAT|O_EXCL)
#   - Section 6.1 (XDG_STATE_HOME placement)
#   - Section 8 invariant: always exit 0 so hooks never block the orchestrator.
#
# Env:
#   PANAKOES_TELEMETRY_DIR  (default ${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/panakoes-telemetry)
#
# Hard rules:
#   - set -uo pipefail (NOT set -e; a missing field or jq parse miss must not abort the hook)
#   - mktemp for collision-free filenames (HIGH-02)
#   - portable nanosecond timestamps via python3 (date +%N is GNU-only; HIGH-02)
#   - single jq invocation for 3 fields (HIGH-04)
#   - tool_use_id fallback for events without one (LOW-06 + HIGH-02)
#   - HOME fallback in logdir resolution (MED-10)
#   - always exit 0 (Section 8 invariant)

set -uo pipefail

LOGDIR="${PANAKOES_TELEMETRY_DIR:-${XDG_STATE_HOME:-${HOME:-/tmp}/.local/state}/panakoes-telemetry}"
SPOOL="$LOGDIR/spool"

# Read stdin event verbatim; do not parse, do not redact. The flusher owns all
# semantic processing. If stdin is empty (no hook event piped in), `cat` returns
# empty and we still emit a sentinel file with `hook_event_name=unknown` so
# operators can see the misfire in the spool.
event=$(cat)

# HIGH-04: single jq invocation extracts the three identity fields. jq startup
# is ~5 ms; three invocations would burn the entire budget by themselves.
# `// "unknown"` / `// ""` defaults guard against missing fields.
#
# We emit three newline-separated values and read line-by-line. The earlier @tsv
# + `IFS=$'\t' read` form silently collapsed empty middle fields because bash's
# `read` treats tab in IFS as whitespace (consecutive whitespace separators
# coalesce). Newline-separated parsing preserves empty fields correctly.
session_id="unknown"
tool_use_id=""
hook_event_name="unknown"
if [ -n "$event" ]; then
  triple=$(printf '%s' "$event" | jq -r '[.session_id // "unknown", .tool_use_id // "", .hook_event_name // "unknown"] | .[]' 2>/dev/null || true)
  if [ -n "$triple" ]; then
    { read -r session_id; read -r tool_use_id; read -r hook_event_name; } <<<"$triple"
  fi
fi

# Sanitize session_id and hook_event_name for filesystem safety. Strip any
# character that isn't alnum/dash/underscore. Claude Code session IDs are UUIDs
# (already safe) but we belt-and-suspender against malformed payloads.
session_id=$(printf '%s' "$session_id" | tr -c 'A-Za-z0-9._-' '_' | head -c 128)
hook_event_name=$(printf '%s' "$hook_event_name" | tr -c 'A-Za-z0-9._-' '_' | head -c 64)
tool_use_id=$(printf '%s' "$tool_use_id" | tr -c 'A-Za-z0-9._-' '_' | head -c 128)

# Per LOW-06 + HIGH-02: events without a tool_use_id (SessionStart, SessionEnd,
# UserPromptSubmit, Stop, PreCompact, Notification, PermissionRequest) get a
# synthetic id-part so the filename collision-avoidance still works.
id_part="${tool_use_id:-${hook_event_name}-$$-${RANDOM}}"

# Ensure the spool subtree exists. mkdir -p is idempotent and cheap (~1 ms).
mkdir -p "$SPOOL/$session_id" 2>/dev/null || true

# HIGH-02: mktemp uses O_CREAT|O_EXCL semantics so the file is unique by
# construction. The 10 X's give 62^10 ~ 8e17 possible suffixes, collision-free
# at any session rate we'll ever hit.
out=$(mktemp -p "$SPOOL/$session_id" "${id_part}-${hook_event_name}-XXXXXXXXXX.json" 2>/dev/null || true)

if [ -n "$out" ]; then
  # Write the raw event JSON. We do not append a trailing newline so the
  # flusher reads a single JSON document per file. printf is the portable
  # write-without-newline; `echo -n` is not portable.
  printf '%s' "$event" >"$out" 2>/dev/null || true
fi

# Section 8 invariant: hooks NEVER block the orchestrator. Even on full failure
# (spool dir unwritable, mktemp failed, stdin malformed), exit 0.
exit 0

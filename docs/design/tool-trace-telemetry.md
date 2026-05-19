# Tool-trace telemetry: design

**Status:** Designed 2026-05-18, awaiting implementation. Gate 1 review applied 2026-05-19 (architect-reviewer run `2026-05-19T00-08-23Z-architect-review-tool-trace`).
**Owner:** Phil + Claude orchestrator
**Triggered by:** Phil's observability ask during the 2026-05-18 marathon session
**Goal:** Per-tool timing + brief tool context + aggregated analytics, with credential redaction so the trace is safe to keep on disk, fronted by a live operator dashboard adopted from `disler/claude-code-hooks-multi-agent-observability`.

## Outline

1. Why this exists
2. What hooks give us (verified, not assumed)
3. Architecture
   1. Full 12-event hook lifecycle, dual-sink flusher
   2. Event schema (W3C Trace Context + OpenTelemetry GenAI naming)
   3. Hook-event table (all 12 captured events)
   4. Brief generation rules (per-tool extraction, gitleaks redaction)
   5. Async shim hooks + background flusher
   6. SQLite-WAL local sink (analytics of record)
   7. JSONL spool (async-hook intake buffer only)
   8. Hook registration (`.claude/settings.json`)
4. Live Dashboard Integration via disler's claude-code-hooks-multi-agent-observability
   1. Why a separate live dashboard
   2. Architecture: dual-sink hook flusher
   3. Payload mapping
   4. Failure handling
   5. Operational setup
   6. License risk acknowledgment
   7. Migration path
5. Post-processing: `scripts/analyze-tool-trace.py`
6. Operational setup (storage paths, env vars, rotation)
7. Performance budget + benchmark
8. Tradeoffs and open questions
9. Deferred to v2
10. Out of scope for v1
11. Implementation plan (next session)

## 1. Why this exists

We need observability into what the Claude orchestrator does, how long each tool call takes, and how that aggregates into higher-order metrics like "time from agent dispatch to PR merge" and "LOC shipped per agent-minute." The 2026-05-18 session produced ~20 merged PRs but the orchestrator had zero structured visibility into where its time went, which tool calls were slow, or which agent dispatches were most productive. Adding that visibility lets us:

1. Identify slow tool patterns to optimize (e.g. a sequence of 12 short `Read` calls that could be one `Grep`).
2. Compare orchestrator-direct work vs sub-agent dispatch ROI.
3. Surface the "agent dispatched at T, PR opened at T+N, PR merged at T+M, with K lines changed" arc as a reusable productivity baseline.
4. Catch regressions in tool performance (a tool that suddenly averages 3x slower is a signal worth investigating).
5. Stream a live operator view to a small dashboard so a human watching a long session sees what the orchestrator is doing in real time.

## 2. What hooks give us (verified, not assumed)

Per the [Claude Code hooks reference](https://code.claude.com/docs/en/hooks), shell-command hooks receive a JSON event on stdin. The common input fields across all events are at minimum:

- `session_id` (UUID for the conversation)
- `tool_name` (e.g. `Bash`, `Edit`, `Agent`, `mcp__plugin_github_github__create_pull_request`) on tool-scoped events
- `tool_input` (the tool's argument object: `command`, `file_path`, `prompt`, etc.) on tool-scoped events
- `tool_use_id` (unique per call; same value appears in pre and post events for the same invocation, which is how we pair them)
- `tool_result` (PostToolUse only: the tool's structured output; see MUST-01 below for the correct field name) <!-- MUST-01: tool_result not tool_response -->
- `tool_use_duration_ms` (PostToolUse only: server-provided duration; see IMP-05) <!-- IMP-05: tool_use_duration_ms -->
- `agent_id`, `agent_type` (populated inside subagent contexts) <!-- IMP-07: agent fields -->
- `permission_mode` (`default` / `plan` / `acceptEdits` / `auto` / `dontAsk` / `bypassPermissions`) <!-- IMP-07: permission_mode -->
- `effort.level` (`low` / `medium` / `high` / `xhigh` / `max` where available) <!-- IMP-07: effort -->

Tool-call failures fire a dedicated `PostToolUseFailure` event with `error.type` and `error.content`, distinct from `PostToolUse`. <!-- MUST-01: PostToolUseFailure as a SEPARATE event -->

Hooks have a synchronous default, which would slow the orchestrator on every operation. We address this with an async-shim architecture (see Section 3.5) so the synchronous portion stays under 5 ms p99 (see Section 7).

A Bash wrapper around the actual shell would let us instrument shell commands inside our `Bash` tool calls (one level deeper). Out of scope for v1; revisit if Bash-tool brevity turns out to lose too much detail.

## 3. Architecture

### 3.1 Full 12-event hook lifecycle, dual-sink flusher <!-- IMP-01: full hook lifecycle -->

We capture all 12 lifecycle events the [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) enumerates for shell hooks. Each event becomes one row in the SQLite-WAL sink and one HTTP POST to the live dashboard (see Section 4). Capturing the full set unlocks per-prompt cost attribution, native subagent bracketing via `SubagentStart` / `SubagentStop`, idle-time exclusion via `Notification`, and gap explanation via `PreCompact`. The two-event design we started with reinvented several of these correlations manually.

### 3.2 Event schema

Each event is one JSON object. The canonical shape uses W3C Trace Context for correlation and adopts OpenTelemetry GenAI semantic conventions for the GenAI-meaningful fields. <!-- IMP-02: W3C trace context --> <!-- IMP-03: OTel GenAI naming -->

```jsonc
{
  // W3C Trace Context (IMP-02). Generated at SessionStart, propagated through SubagentStart/Stop.
  // trace_id is 32-hex (16 bytes); span_id is 16-hex (8 bytes); parent_span_id links the tree.
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id":  "00f067aa0ba902b7",
  "parent_span_id": "b9c7c989f97918e1",

  // Event identity (descriptive names; no terse aliases per Phil's Gate-1.5 decision)
  "timestamp":         "2026-05-18T23:55:11.123Z",
  "hook_event_name":   "PostToolUse",            // one of the 12 hook event names; see Section 3.3
  "session_id":        "abc-uuid",               // Claude Code session_id

  // OpenTelemetry GenAI semantic conventions (IMP-03; canonical for any field with an OTel equivalent)
  "gen_ai.system":             "anthropic",
  "gen_ai.operation.name":     "execute_tool",
  "gen_ai.tool.name":          "Bash",
  "gen_ai.tool.call.id":       "toolu_01XYZ",   // the single canonical tool_use_id; no `seq` alias
  "gen_ai.tool.type":          "function",
  "gen_ai.agent.id":           "general-purpose-001",
  "gen_ai.agent.name":         "general-purpose",
  "gen_ai.request.model":      "claude-opus-4-7",
  "gen_ai.usage.input_tokens":  1234,
  "gen_ai.usage.output_tokens": 567,

  // Project-specific fields (no OTel equivalent; keep descriptive long names)
  "agent_id":             "general-purpose-001",
  "agent_type":           "general-purpose",        // or Plan / Explore / feature-dev:code-explorer / etc.
  "permission_mode":      "acceptEdits",
  "effort_level":         "high",
  "brief":                "git push -u origin chore/deps-typescript-6-admin",  // gitleaks-redacted summary (IMP-04)
  "tool_use_duration_ms": 666,                  // IMP-05: server-provided, not computed
  "success":              true,                 // false only ever set by PostToolUseFailure
  "out_len":              234,                  // see MUST-01 for the field-name fix
  "err_len":              0
}
```

**Naming convention (Phil's Gate-1.5 decision, 2026-05-19): OTel-only.** We use OpenTelemetry GenAI semantic convention names for any field with an OTel equivalent (`gen_ai.system`, `gen_ai.tool.call.id`, `gen_ai.usage.input_tokens`, etc.); project-specific fields use descriptive long names (`hook_event_name`, `session_id`, `agent_type`, `brief`). No terse aliases anywhere (the prior draft maintained both `t`/`timestamp`, `ev`/`hook_event_name`, etc.; that was dropped to keep one canonical name per concept). Trade-off: the JSONL spool and disler-payload bytes-per-event grow somewhat, in exchange for direct exportability to Datadog v1.37+, Grafana Loki, Tempo, Honeycomb (no column-mapping script needed). See [W3C Trace Context spec](https://www.w3.org/TR/trace-context/) and [OpenTelemetry GenAI agent spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/).

Trace IDs and span IDs are generated in bash with the one-liner from [bash traceparent generation](https://www.cicoria.com/generate-opentelemetry-compliant-traceparent-tracecontext-headers-using-bash/):

```bash
TRACE_ID="$(tr -dc 'a-f0-9' </dev/urandom | head -c 32)"
SPAN_ID="$( tr -dc 'a-f0-9' </dev/urandom | head -c 16)"
```

### 3.3 Hook-event table <!-- IMP-01: all 12 events with semantics -->

All 12 events are captured. Each fires its own shell hook that emits one event row.

| `hook_event_name`    | Fires when                                                                                          | Pairs / brackets with                          | Notes                                                                                          |
| -------------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| `SessionStart`       | The orchestrator session begins.                                                                    | `SessionEnd`                                   | Generates `trace_id` once, propagates to all subsequent events in the session.                 |
| `SessionEnd`         | The orchestrator session ends (Ctrl+D, `/exit`, harness teardown).                                  | `SessionStart`                                 | Deterministic boundary; replaces first-pre / last-post heuristics for session-duration metric. |
| `UserPromptSubmit`   | User submits a prompt (turn boundary input).                                                        | next `Stop`                                    | Partitions tool calls per-prompt: "what did each user prompt cost?"                            |
| `Stop`               | The orchestrator finishes its turn (end of an assistant response).                                  | prior `UserPromptSubmit`                       | Clean per-turn bracketing.                                                                     |
| `PreToolUse`         | Before any tool invocation.                                                                         | `PostToolUse` via `gen_ai.tool.call.id`        | `tool_input` available here; `brief` extracted here.                                           |
| `PostToolUse`        | After a tool invocation that succeeded (returned a `tool_result`).                                  | `PreToolUse` via `gen_ai.tool.call.id`         | Carries `tool_result` (see MUST-01), `tool_use_duration_ms` (IMP-05).                          |
| `PostToolUseFailure` | After a tool invocation that failed (separate event from `PostToolUse`, never overlaps).            | `PreToolUse` via `gen_ai.tool.call.id`         | Carries `error.type`, `error.content`. Error-rate metric reads from this event only.           |
| `SubagentStart`      | A subagent dispatch begins.                                                                         | `SubagentStop` via `agent_id` | Replaces the brittle "scan for `Agent` then look for downstream `gh pr create`" heuristic.     |
| `SubagentStop`       | A subagent dispatch completes.                                                                      | `SubagentStart`               | Carries `agent_transcript_path` for post-hoc transcript analysis.                              |
| `PreCompact`         | The orchestrator compacts the conversation context window.                                          | (no pair)                     | Explains gaps in tool-call sequences: a compact at minute 47 explains zero events 47-52.       |
| `Notification`       | Permission prompt or idle-prompt to the user.                                                       | (no pair)                     | Marks "waiting on Phil" intervals; lets the analyzer exclude idle time from productivity.      |
| `PermissionRequest`  | The orchestrator requests an elevated permission for a specific tool.                               | (no pair)                     | Adjacent to `Notification`; distinct event covers programmatic permission asks.                |

### 3.4 Brief generation rules

The `brief` field is the orchestrator's "what was this call about" summary, capped per-tool and gitleaks-redacted before storage. <!-- IMP-04: gitleaks as the redactor -->

**Brief generation rules (in priority order):**

1. **Per-tool field selection** (extract the most informative field, skip the noise):
   - `Bash` -> `command` (full, before redaction)
   - `Edit` / `Write` / `Read` -> `file_path` (always safe, never contains secrets)
   - `Agent` -> `description` + `subagent_type` joined with `/`
   - `Grep` -> `pattern` + `path`
   - `Glob` -> `pattern`
   - `WebFetch` / `WebSearch` -> `url` or `query`
   - `mcp__*` MCP tools -> `<server>/<tool>: <tool_input.params...>` walked recursively; see MUST-03 below.
   - default -> first 256 chars of `JSON.stringify(tool_input)`

   <!-- MUST-03: MCP nested inputs -->
   **MCP tool inputs are nested.** The hook payload for an MCP call is shaped:

   ```jsonc
   {
     "tool_name": "mcp__plugin_github_github__create_pull_request",
     "tool_input": {
       "server": "github",
       "tool":   "create_pull_request",
       "params": { "title": "...", "body": "...", "head": "...", "base": "..." }
     }
   }
   ```

   The interesting content is at `tool_input.params.*`, not at `tool_input.*`. The brief-extractor walks the nested structure: `prepend "<server>/<tool>: "` then `tostring(tool_input.params)`. Treating `tool_input` as flat (as a draft revision did) produces useless wrapper-dumps of `{"server":"github","tool":"create_pull_request","params":{...}}` and burns the per-tool cap on the wrapper instead of the meaningful payload.

2. **Redact via gitleaks-as-a-library** (IMP-04). We replace the hand-rolled 10-pattern `sed` set with `gitleaks detect --no-git --pipe --redact`, which ships [160+ rules](https://github.com/gitleaks/gitleaks) maintained continuously. Output is JSON-structured and includes the rule name, so redacted markers become `<REDACTED:gitleaks:aws-access-token>` etc. Coverage we gain over the hand-rolled set: GitHub fine-grained PATs (`github_pat_<22>_<59>`), Stripe restricted keys (`rk_live_` / `rk_test_`), DigitalOcean, Cloudflare, Slack webhook, Twilio, SendGrid, MailGun, Datadog, PagerDuty, Linear, Notion, Figma, Vercel, npm token, pypi token, Docker Hub token, SSH private-key bodies (not just the BEGIN header), database connection strings, and generic high-entropy strings paired with surrounding-keyword context for far fewer false positives than naked regex. See [secret-scanner comparison](https://appsecsanta.com/sast-tools/gitleaks-vs-trufflehog).

   **Latency note:** gitleaks startup is ~50-100 ms cold, heavier than `sed`. Two mitigations: (a) the flusher runs gitleaks once per drained batch, not once per event, amortizing startup across N events; (b) the async-shim architecture in Section 3.5 absorbs the redaction cost entirely off the synchronous path, so the hook latency the orchestrator sees stays under 5 ms p99 regardless of redactor cost. The flusher's gitleaks subprocess can also be kept warm via a long-running daemon wrapper (FIFO + tight read loop) if batch-mode is not enough.

3. **Truncate after redaction** to a sensible cap. 80 chars is too little. Caps: **2048 chars** for `Bash` (commands can be long), **1024 chars** for `Agent` (descriptions plus subagent_type), **512 chars** for everything else. Append `...` if truncated.

4. **JSON-encode the brief** so newlines, quotes, control characters do not break the JSONL line.

### 3.5 Async shim hooks + background flusher <!-- IMP-06: all hooks run async -->

All 12 hooks run async (fire-and-forget background process). The shell hook command is now a tiny shim that writes the raw event JSON to a spool directory and returns within milliseconds; a separate background flusher process reads the spool, runs gitleaks redaction in batch, computes the brief, and dual-writes to the SQLite-WAL sink and the disler dashboard.

Spool layout:

```
${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry/spool/<session_id>/<tool_use_id>-<hook_event_name>.json
```

The shim:

```bash
#!/usr/bin/env bash
# trace-shim.sh: write raw event to spool, return in <5ms.
set -uo pipefail
LOGDIR="${PANAKOES_TELEMETRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry}"
SPOOL="$LOGDIR/spool"
mkdir -p "$SPOOL"

# Read stdin event verbatim; do not parse, do not redact, do not jq.
# A single write under PIPE_BUF (4 KB) is atomic on POSIX local filesystems (see MUST-04).
# Bash >> opens with O_APPEND, which is the POSIX-required precondition.
event=$(cat)
session_id=$(printf '%s' "$event" | jq -r '.session_id // "unknown"')
tool_use_id=$(printf '%s' "$event" | jq -r '.tool_use_id // "no-id"')
hook_event_name=$(printf '%s' "$event" | jq -r '.hook_event_name // "unknown"')
mkdir -p "$SPOOL/$session_id"
printf '%s\n' "$event" > "$SPOOL/$session_id/$(date -u +%s%N)-$tool_use_id-$hook_event_name.json"
exit 0
```

The flusher (`scripts/telemetry-flusher.py`, runs as a long-lived background process under `systemd --user` or a launchctl LaunchAgent):

1. Polls `$SPOOL/*/` every 250 ms with inotify fallback.
2. Drains all complete files into an in-memory batch.
3. Runs gitleaks on the batch (one subprocess invocation per batch, not per event).
4. Computes the brief per the rules in Section 3.4.
5. Inserts into the SQLite-WAL sink (Section 3.6).
6. POSTs to the disler dashboard at `$DISLER_URL/events` (Section 4).
7. Marks failures distinctly so a future backfill tool can replay disler-side gaps without re-running gitleaks.

The flusher's `disler_pushed_at` column starts NULL and is set on successful POST, so we can detect and (optionally) replay un-pushed events without losing the durable SQLite record. The hook itself never blocks on disler reachability.

### 3.6 SQLite-WAL local sink (analytics of record) <!-- IMP-10: SQLite-WAL as primary local sink -->

Per IMP-10, the local sink of record is a SQLite database in WAL mode. The JSONL spool stays only as the async-hook intake buffer (small, drained every 250 ms; never accumulates more than a few seconds of events).

Database path:

```
${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry/telemetry.sqlite
```

Schema sketch:

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE events (
  id                          INTEGER PRIMARY KEY,
  trace_id                    TEXT NOT NULL,    -- W3C trace_id, 32 hex
  span_id                     TEXT NOT NULL,    -- W3C span_id, 16 hex
  parent_span_id              TEXT,             -- W3C parent_span_id, 16 hex, NULL for root span
  timestamp                   TEXT NOT NULL,    -- ISO 8601 UTC
  hook_event_name             TEXT NOT NULL,    -- one of the 12 hook event names
  session_id                  TEXT NOT NULL,    -- Claude Code session_id
  tool_use_id                 TEXT,             -- maps to gen_ai.tool.call.id
  tool_name                   TEXT,             -- maps to gen_ai.tool.name
  brief                       TEXT,             -- redacted, truncated
  tool_use_duration_ms        INTEGER,          -- from PostToolUse payload (IMP-05)
  success                     INTEGER,          -- 1/0; NULL for non-tool events
  out_len                     INTEGER,
  err_len                     INTEGER,
  agent_id                    TEXT,
  agent_type                  TEXT,
  permission_mode             TEXT,
  effort_level                TEXT,
  gen_ai_request_model        TEXT,
  gen_ai_usage_input_tokens   INTEGER,
  gen_ai_usage_output_tokens  INTEGER,
  payload                     TEXT,             -- full event JSON, redacted, as a fallback / for ad-hoc query
  disler_pushed_at            TEXT              -- ISO 8601 when POSTed; NULL if not yet / failed
);

CREATE INDEX idx_events_session_id   ON events (session_id, timestamp);
CREATE INDEX idx_events_trace        ON events (trace_id, span_id);
CREATE INDEX idx_events_tool         ON events (tool_name, timestamp);
CREATE INDEX idx_events_agent        ON events (agent_id, timestamp);
CREATE INDEX idx_events_disler_null  ON events (disler_pushed_at) WHERE disler_pushed_at IS NULL;
```

W3C trace fields are first-class columns on our side (they fit the relational model and make the parent-child join cheap). On disler's side they live inside the `payload` blob because disler's schema does not have columns for them. See [SQLite WAL](https://sqlite.org/wal.html).

### 3.7 JSONL spool

The JSONL spool stays under `${XDG_STATE_HOME}/panakoes-telemetry/spool/` as the async intake buffer only. It is drained every 250 ms and never accumulates more than a few seconds of events. The analyzer does not read the spool directly; it reads the SQLite database. The spool exists to decouple the synchronous hook from the redaction + writer pipeline.

<!-- MUST-04: O_APPEND atomicity -->
**Atomic-append assertion:** The shim writes one file per event rather than appending to a shared file, so cross-write interleaving is structurally impossible. We retain `>>` semantics with `O_APPEND` for any internal log file the flusher writes (for instance, the flusher's own stderr log). Per [POSIX](https://pubs.opengroup.org/onlinepubs/9699919799/functions/V2_chap02.html#tag_15_09_07) and [empirical kernel behavior](https://www.notthewizard.com/2014/06/17/are-files-appends-really-atomic/), writes of length `<= PIPE_BUF` (4 KB) issued through a file opened with `O_APPEND` are atomic on POSIX local filesystems (ext4, btrfs, xfs, tmpfs). Bash `>>` opens with `O_APPEND`. Linux 4.2.6+ raised the practical atomicity ceiling on ext4 to 1 MB, but we do not rely on the higher bound.

**Test that proves the assertion:** `tests/telemetry/test_append_atomicity.py` spawns 64 concurrent writers, each issuing 10,000 1-KB lines through `>>` against a file on the configured `XDG_STATE_HOME` filesystem. After all writers exit, the test parses the file as JSONL and asserts every line is well-formed JSON with no interleaving artifacts (no `}{ `, no truncated string literals, no zero-length lines). The test runs in CI on the Linux runner (ext4) and is gated on `df -T "$XDG_STATE_HOME" | awk 'NR==2 {print $2}'` returning `ext4`, `btrfs`, `xfs`, or `tmpfs`. If the filesystem is `9p` / `nfs` / `cifs` / `smbfs`, the flusher fails fast at startup with an explicit error message; we do not attempt to support non-local filesystems.

**Startup filesystem check** (in the flusher init):

```bash
fstype=$(df -T "$LOGDIR" | awk 'NR==2 {print $2}')
case "$fstype" in
  ext4|btrfs|xfs|tmpfs) : ;;
  *) echo "panakoes-telemetry: $LOGDIR is on $fstype, which does not guarantee O_APPEND atomicity. Move to a local POSIX filesystem (ext4/btrfs/xfs/tmpfs). Refusing to start." >&2; exit 1 ;;
esac
```

This catches the case where Phil moves the project root to `/mnt/c/...` (9P) or mounts a network share for the state dir.

### 3.8 Hook registration: `.claude/settings.json`

```json
{
  "hooks": {
    "PreToolUse":       [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "PostToolUse":      [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "PostToolUseFailure":[{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "SubagentStart":    [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "SubagentStop":     [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "SessionStart":     [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "SessionEnd":       [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "PreCompact":       [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "Notification":     [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "Stop":             [{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }],
    "PermissionRequest":[{ "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh" }] }]
  }
}
```

<!-- MUST-02: matcher regex fix -->
**Matcher note:** Claude Code hooks treat the `matcher` value as a JavaScript regex, not a glob. A literal `"*"` is not valid JS regex (a quantifier with nothing to quantify) and the hook registration is silently rejected, so no events fire. The safe forms are (a) omit the `matcher` key entirely (the reference explicitly says "omitted matcher matches all"), or (b) set `"matcher": ".*"`. We omit it everywhere above so the wiring works on every Claude Code version regardless of how strict the matcher parser is. If a future hook needs tool-name filtering, use a real regex like `"^(?!TaskList|TaskGet$).+"` (JS regex, supports lookahead).

## 4. Live Dashboard Integration via disler's claude-code-hooks-multi-agent-observability

### 4.1 Why a separate live dashboard

We adopt [`disler/claude-code-hooks-multi-agent-observability`](https://github.com/disler/claude-code-hooks-multi-agent-observability) (1.4k stars, actively maintained as of 2026-05-18) as the live operator dashboard for the Panakoes telemetry stack. Disler's architecture is Python hooks -> HTTP POST -> Bun server -> SQLite WAL -> WebSocket -> Vue 3 dashboard; it captures all 12 hook event types, namespaces events by `source_app`, and renders a real-time stream with per-tool emoji indicators and MCP-tool decorations. It is direct prior art for exactly this use case (see RES-01 in the architect review).

We do NOT vendor its code (the repository ships with no LICENSE file as of 2026-05-18; `https://github.com/disler/claude-code-hooks-multi-agent-observability/blob/main/LICENSE` returns 404, which under default GitHub policy means "all rights reserved" and is a legal risk for a public MIT repo like Panakoes). We treat disler as a runtime dependency reached over HTTP. Our hook capture, gitleaks redaction, async flusher, and SQLite-WAL sink are built in-tree and remain the source of truth.

### 4.2 Architecture: dual-sink hook flusher

```
Claude Code hook fires
    |
    v
[ async shim writes raw event to spool/ ]   <-- <5ms p99
    |
    v
[ background flusher reads spool/ ]
    |
    +--> [ secret-redact via gitleaks (batch, IMP-04) ]
    |       |
    |       +--> [ SQLite-WAL local sink (analytics-of-record, IMP-10) ]
    |       |
    |       +--> [ HTTP POST to disler server at $DISLER_URL (live dashboard) ]
    |
    (both sinks see the SAME redacted event; no secrets ever leave the machine)
```

Both sinks consume the same redacted, brief-truncated event payload. Disler sees a strict subset of what the SQLite database stores; nothing that fails our redaction reaches either sink, because the flusher redacts BEFORE either write.

### 4.3 Payload mapping

Disler's server accepts a fixed JSON shape per its README. Our internal event shape (richer per IMP-02, IMP-03, IMP-05, IMP-07) gets projected to disler's expected fields; anything disler does not have a column for goes into the `payload` blob.

| Disler field      | Disler type   | Mapped from our event                                                                |
| ----------------- | ------------- | ------------------------------------------------------------------------------------ |
| `source_app`      | TEXT          | Literal `"panakoes"` (namespaces our events on any shared disler server).            |
| `session_id`      | TEXT          | `session_id` (Claude Code session_id).                                               |
| `hook_event_type` | TEXT          | `hook_event_name` (one of the 12 hook event names).                                  |
| `payload`         | TEXT (JSON)   | The full redacted event JSON, including W3C trace fields, OTel GenAI fields, agent context. |
| `timestamp`       | INTEGER (ms)  | Unix milliseconds parsed from our `timestamp` field.                                 |
| `model_name`      | TEXT          | `gen_ai.request.model` if set, else NULL.                                            |
| `tool_name`       | TEXT          | `gen_ai.tool.name` from PreToolUse / PostToolUse / PostToolUseFailure events; NULL otherwise. |
| `tool_use_id`     | TEXT          | `gen_ai.tool.call.id` where applicable.                                              |
| `error`           | TEXT          | For PostToolUseFailure: `error.type` + ": " + `error.content`, truncated to 512 chars. |
| `agent_id`        | TEXT          | `agent_id` (IMP-07) where set.                                                       |

Trace-context fields (`trace_id`, `span_id`, `parent_span_id`) and OTel GenAI fields live in `payload` only on disler's side; on our SQLite side they get first-class columns. The flusher serializes the projection once per event and posts to disler asynchronously while the SQLite insert commits synchronously on our side.

### 4.4 Failure handling

Disler server unreachable, slow, or returning 5xx:

- Log the failure to stderr (visible in the flusher's own log under `${XDG_STATE_HOME}/panakoes-telemetry/flusher.log`).
- DO NOT block the hook (the hook has already returned to the orchestrator).
- DO NOT spool retries to disler (disler is a live dashboard, missed live events are acceptable; SQLite has the durable record).
- Mark the affected SQLite rows with `disler_pushed_at = NULL` so a future backfill tool can scan the index and replay if Phil wants to.
- Health-check: the flusher pings `$DISLER_URL/health` every 60 s and surfaces the up/down state via `scripts/telemetry-status.sh` (and a future local UI). A long-running outage produces one stderr line per minute, not one per event.

This is intentional asymmetry: we are willing to lose live-dashboard visibility under disler outages but we are not willing to lose analytics-of-record.

### 4.5 Operational setup

Where disler's server runs:

- **Solo dev (current):** localhost, on Phil's box, Bun server on port 4000.
- **Multi-machine (eventual):** a small EC2 instance (t4g.nano, ~$5/month) that multiple Claude sessions can POST to.

Environment variables:

- `DISLER_URL` (default `http://localhost:4000`): the dashboard server endpoint. Override for remote.
- `DISLER_ENABLED` (default `false` initially): explicit on-switch. Flip to `true` once the server is up. With `DISLER_ENABLED=false`, the flusher skips the POST step but still writes SQLite, so we can stand up the local sink before the dashboard.
- `PANAKOES_TELEMETRY_DIR` (default `${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry`): root of the spool, SQLite, archive, and disler-server checkout.

Setup command: `scripts/telemetry-setup.sh` does the following idempotently:

1. Creates `${PANAKOES_TELEMETRY_DIR}` and subdirectories (`spool/`, `archive/`, `disler/`).
2. Clones disler's repo into `${PANAKOES_TELEMETRY_DIR}/disler/` if not already present.
3. Pulls the latest disler commit (optional `--no-update` flag to pin).
4. Installs Bun (if absent) and runs `bun install` in the disler checkout.
5. Starts the Bun server on `$DISLER_PORT` (default 4000) as a `systemd --user` unit (or LaunchAgent on macOS).
6. Starts the Vite dashboard on `$DISLER_DASHBOARD_PORT` (default 5173).
7. Opens the dashboard in the default browser (skip with `--no-browser`).
8. Initializes the SQLite database with the schema in Section 3.6 if not present.
9. Starts the flusher daemon if not running.

The setup script is the ONLY place we touch disler's tree; we do not check disler's code into our repo, and the disler checkout lives outside the Panakoes worktree.

### 4.6 License risk acknowledgment

As of 2026-05-18, [`https://github.com/disler/claude-code-hooks-multi-agent-observability/blob/main/LICENSE`](https://github.com/disler/claude-code-hooks-multi-agent-observability/blob/main/LICENSE) returns 404. Default GitHub state without a LICENSE file is "all rights reserved." We mitigate by NEVER vendoring his code into our public MIT repo; we only invoke his server over HTTP at runtime, which is well-established to not require a license grant from his side. If disler later adds a LICENSE we may revisit (vendoring a fork into our tree would simplify the setup script). If disler archives or removes the repo, our SQLite-WAL sink continues unaffected (it is the analytics-of-record); we lose only the live dashboard.

### 4.7 Migration path

If we later replace disler's dashboard (build our own UI, point at Grafana, integrate Langfuse), the dual-sink architecture means we swap only the HTTP target in the flusher. Our SQLite-WAL data is unchanged; trace IDs, span IDs, OTel GenAI field names, and agent attribution all carry forward. Estimate: a few hours of work to point the flusher at a new HTTP sink, plus whatever the new dashboard's schema mapping needs. The OTel GenAI naming (IMP-03) makes any OTel-aware backend (Datadog, Grafana Loki / Tempo, Honeycomb, Langfuse) a near-zero-config target.

## 5. Post-processing: `scripts/analyze-tool-trace.py`

Run on demand or via cron. Reads from the SQLite-WAL sink (Section 3.6), computes:

### Per-session summary
- Total tool calls
- Total wall-clock duration of all tool calls (sum of `tool_use_duration_ms` per IMP-05, not computed from pre/post timestamps)
- Per-tool call count + p50 + p95 + max duration
- Top 10 slowest individual calls (tool, brief, duration)
- Error rate per tool (`PostToolUseFailure` count / `PreToolUse` count, per tool name; this metric is correct only because PostToolUseFailure is a distinct event per MUST-01)

### Agent lifecycle correlation (the headline metric)

Native via `SubagentStart` / `SubagentStop` (IMP-01); no more brittle "scan for PR-create after Agent call" heuristic.

For each `SubagentStart`/`SubagentStop` pair:
1. Read `agent_id`, `agent_type`, `started_at`, `finished_at`, `agent_transcript_path`.
2. Filter `events` rows on `agent_id = ?` to find the subagent's tool calls (exact attribution, no inference).
3. Scan the subagent's tool calls for `mcp__plugin_github_github__create_pull_request` or `Bash` calls containing `gh pr create`; extract PR number from the result payload.
4. Query `gh pr view <N> --json mergedAt,additions,deletions` (cached so we are not hammering the API across re-runs).
5. Compute: `agent_duration_ms`, `dispatch_to_pr_open_ms`, `pr_open_to_merge_ms`, `loc_added`, `loc_removed`, `loc_per_agent_minute`.
6. Optionally walk the `agent_transcript_path` for additional context (which prompts the subagent processed, how many compactions, etc.).

### Per-prompt cost attribution

For each `UserPromptSubmit` -> `Stop` interval (IMP-01), aggregate the tool calls bracketed by the pair and report per-prompt wall-clock, per-prompt tool-time, idle-time exclusion (subtract intervals bracketed by `Notification`), and effort-normalized productivity (`tool_use_duration_ms` weighted by `effort_level`).

### Weekly markdown report

`scripts/analyze-tool-trace.py --week 2026-W20 --out docs/analytics/2026-W20.md` produces:

```markdown
# Tool trace analytics: 2026-W20

## Summary
- Sessions: 12
- Total tool calls: 3,847
- Wall-clock tool time: 18h 24m
- Idle time excluded (Notification events): 2h 11m
- Agents dispatched: 23
- PRs opened: 19
- PRs merged: 17

## Per-tool aggregates
| Tool | Calls | p50 (ms) | p95 (ms) | max (ms) | Err rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bash | 1,204 | 1,250 | 8,400 | 184,200 | 2.1% |
| Read | 892 | 30 | 180 | 4,100 | 0.1% |
| Agent | 23 | 264,000 | 1,650,000 | 1,780,000 | 4.3% |
...

## Slowest individual calls
| When | Tool | Duration | Brief |
| --- | --- | ---: | --- |
| Mon 21:14 | Bash | 184s | `make test-local` |
...

## Agent productivity
| Dispatch | Agent | Duration | PR | LOC | LOC/min |
| --- | --- | ---: | --- | ---: | ---: |
| Mon 21:36 | dependency-updater | 4m28s | #367 | 8 | 1.8 |
| Mon 22:14 | KMS W2-T1 builder | 6m04s | #365 | 209 | 34.5 |
...

## Per-prompt cost (top 10 most expensive)
| Time | Prompt fragment | Tool calls | Wall-clock | Idle | Net work |
| --- | --- | ---: | ---: | ---: | ---: |
| Mon 22:14 | "drain the PR backlog and..." | 487 | 2h 06m | 8m | 1h 58m |
...
```

The analyzer can ingest the SQLite database directly via `sqlite3` or via DuckDB's `ATTACH 'telemetry.sqlite' AS t (TYPE SQLITE)` for ad-hoc analytical queries; the choice is a script-internal detail.

## 6. Operational setup

### 6.1 Storage paths <!-- IMP-08: XDG_STATE_HOME --> <!-- IMP-09: rotation policy -->

All telemetry state lives under `${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry/`:

```
${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry/
├── telemetry.sqlite          # SQLite-WAL analytics-of-record (Section 3.6)
├── telemetry.sqlite-wal      # WAL journal (managed by SQLite)
├── telemetry.sqlite-shm      # Shared memory file (managed by SQLite)
├── spool/                    # Async-hook intake buffer (Section 3.7)
│   └── <session_id>/<tool_use_id>-<hook_event_name>.json   # one file per event, drained every 250 ms
├── archive/                  # Rotated SQLite snapshots
│   └── 2026-05-18.sqlite.zst # zstd-compressed daily archives
├── disler/                   # Cloned disler repo (Section 4.5)
└── flusher.log               # Flusher's own stderr log
```

We use `$XDG_STATE_HOME` (not `/tmp`, which is wiped on reboot or WSL shutdown and has no rotation; and not `~/.config`, which per the XDG spec is for config, not state). Override via `$PANAKOES_TELEMETRY_DIR` for non-XDG layouts or alternate volumes.

### 6.2 Rotation policy

The SQLite database rotates when EITHER condition first becomes true:

- Size exceeds 1 GB on disk, OR
- Age of the oldest row exceeds 90 days.

Rotation procedure (run nightly by the flusher, or on demand via `scripts/telemetry-rotate.sh`):

1. `VACUUM INTO 'archive/${YYYY-MM-DD}.sqlite'` to write a defragmented snapshot atomically.
2. `zstd -19 -T0 'archive/${YYYY-MM-DD}.sqlite'` to compress (typical ratio ~10x for JSONL-shaped data).
3. `rm 'archive/${YYYY-MM-DD}.sqlite'` after compression succeeds.
4. `DELETE FROM events WHERE t < datetime('now', '-90 days')` against the live database.
5. `VACUUM` the live database to reclaim space.
6. `find archive/ -name '*.sqlite.zst' -mtime +365 -delete` to purge archives older than one year.

A heavy session is ~10K events with ~500 bytes per row uncompressed; a year of similar activity is ~1 GB uncompressed, ~100 MB zstd-compressed in the archive. Comfortable budget.

### 6.3 Environment variables

| Variable                    | Default                                                            | Purpose                                                      |
| --------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------ |
| `PANAKOES_TELEMETRY_DIR`    | `${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry`         | Root of all telemetry state (Section 6.1).                   |
| `DISLER_URL`                | `http://localhost:4000`                                            | Disler server endpoint (Section 4.5).                        |
| `DISLER_ENABLED`            | `false`                                                            | On-switch for the disler POST; SQLite writes always run.     |
| `DISLER_PORT`               | `4000`                                                             | Bun server port (setup script reads this).                   |
| `DISLER_DASHBOARD_PORT`     | `5173`                                                             | Vite dashboard port.                                         |
| `CLAUDE_TRACE_DEBUG`        | `0`                                                                | When `1`, flusher logs full event JSON to stderr (verbose).  |

## 7. Performance budget <!-- IMP-12: hook-latency benchmark -->

**Hard ceiling on the synchronous-hook portion: 5 ms p99.** The shim does a single `cat` of stdin, three `jq -r` extractions, a `mkdir -p`, and one file write. The hook returns to the orchestrator within milliseconds regardless of redaction or sink cost. The async background work (gitleaks redaction in batch, SQLite WAL insert, HTTP POST to disler) has no ceiling and runs in the flusher process.

**Benchmark to enforce the budget:** `scripts/bench-hook.sh` runs the shim against a fixture set covering each tool's input shape, captures p50 / p95 / p99 / max wall-clock, and fails the budget gate if p99 exceeds 5 ms.

```bash
#!/usr/bin/env bash
# bench-hook.sh: enforce the synchronous-hook budget.
set -euo pipefail

FIXTURE_DIR="tests/telemetry/fixtures"
SHIM="${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-shim.sh"

for fixture in "$FIXTURE_DIR"/*.json; do
  name=$(basename "$fixture" .json)
  hyperfine \
    --shell=none \
    --warmup 10 \
    --runs 200 \
    --export-json "bench-results/$name.json" \
    --input "$fixture" \
    "$SHIM"
done

python3 scripts/check-bench-budget.py \
  --results-dir bench-results \
  --p99-ceiling-ms 5
```

The fixture set lives at `tests/telemetry/fixtures/*.json` and includes one fixture per tool (Bash, Edit, Read, Write, Grep, Glob, Agent, WebFetch, WebSearch, plus one representative `mcp__plugin_github_github__create_pull_request` and one `mcp__plugin_playwright_playwright__browser_navigate`). `check-bench-budget.py` aggregates results and exits non-zero if any tool's p99 exceeds the ceiling. The benchmark runs in CI on PR pushes that touch `.claude/hooks/**`.

**Note on the async-flusher's HTTP call to disler:** the POST to `$DISLER_URL` is unbounded in latency and explicitly does NOT contribute to the hook budget. The flusher absorbs it asynchronously off the hot path. A disler outage means slower batch drain (rows accumulate in spool/), not slower hook returns.

**Note on jq fork overhead:** per [jq performance discussion](https://news.ycombinator.com/item?id=24468874), jq startup is ~5 ms per invocation. The shim invokes jq three times to extract `session_id`, `tool_use_id`, and `hook_event_name`; the benchmark will tell us whether to collapse these into a single jq invocation (`jq -r '[.session_id, .tool_use_id, .hook_event_name] | @tsv'` then `read session_id tool_use_id hook_event_name <<<"$line"`) or to rewrite the shim in Python (~30 ms cold startup, but no compounded fork overhead and the redaction can stay in-process). We decide based on the bench-hook.sh output, not on a-priori assertion.

## 8. Tradeoffs and open questions

| Concern | Resolution |
| --- | --- |
| Hook latency | Hard ceiling 5 ms p99 on the synchronous-hook portion; enforced by `scripts/bench-hook.sh` (Section 7). All redaction and writes happen in the async flusher. |
| Secret-detection false positives | Acceptable; gitleaks pairs regex with surrounding keyword context to keep false-positive rate manageable. Better to redact than leak. |
| Secret-detection false negatives | Significantly reduced via gitleaks 160+ rules vs the prior hand-rolled 10-pattern set (IMP-04). Residual risk mitigated by: (a) the SQLite file is gitignored and lives outside the project tree; (b) the dashboard and SQLite see identical redacted payloads; (c) a future nightly cron could re-scan archives with newer gitleaks rule versions. |
| Per-tool brief cap (2048 / 1024 / 512) | Subject to revision once real traces show the distribution of brief lengths. Cheap to tune. |
| Storage growth | A heavy session is ~10K events; a year is ~1 GB SQLite uncompressed (Section 6.2). Rotation keeps the live database under 1 GB and 90 days. |
| Hook failure blast | The shim is `set -uo pipefail` (not `set -e`); the file write is wrapped in a defensive idiom; the hook exits 0 regardless. If a hook script disappears entirely, Claude Code logs a warning but does not block the tool call. |
| Sensitive `tool_result.content` | We log `out_len` only, never the response body. The body might contain anything (secrets file contents, AWS keys printed during debugging, etc.). |
| Privacy when sharing traces | A trace database should never be shared without first re-running the redactor on the `payload` column AND a human eyeball pass. Document this on top of the analytics report. |
| Filesystem atomicity bound | Asserted via flusher startup check: refuses to start on 9P / NFS / SMB (Section 3.7). Tested via `tests/telemetry/test_append_atomicity.py` (Section 3.7, MUST-04). |
| Disler upstream license / availability | We do not vendor disler's code (Section 4.6). If upstream archives the repo or changes terms unfavorably, the SQLite sink continues; we lose only the live dashboard. |

## 9. Deferred to v2

- **PII redaction beyond credential scanning.** Phil's call: out of scope for the initial implementation. The architect-reviewer (IMP-11) recommended adding email / IPv4-IPv6 / phone / credit-card detection to the redactor; we defer this to v2. PII redaction beyond credential scanning is deferred to v2; see followups.
- Real-time dashboards beyond the disler view (Grafana / Langfuse adoption, multi-machine aggregation, web-based weekly reports).
- Cross-machine correlation (each machine's database stays local; the disler-on-EC2 plan in Section 4.5 is the eventual story but is itself a v2 item).
- Auto-blocking on detected secrets in `tool_input` (the PreToolUse hook could reject a tool call whose input matches a high-confidence secret pattern). Useful but risky (false positives block real work).

## 10. Out of scope for v1

- Bash command sub-instrumentation (the wrapper-script-around-shell idea Phil floated). Revisit if Bash-tool brevity proves too coarse in practice.
- A full streaming pipeline beyond the disler dashboard. Disler covers the live-view need; a Grafana/Loki feed would be v3+.
- Modifying disler upstream. We treat it as a black-box runtime dependency.

## 11. Implementation plan (next session)

1. Create `.claude/hooks/trace-shim.sh` (single file, used for all 12 hook events). Make executable.
2. Add `.claude/settings.json` with the 12 hook registrations (omit `matcher` key entirely per MUST-02).
3. Add `${PANAKOES_TELEMETRY_DIR}/**` to `.gitignore` defense in depth.
4. Write `scripts/telemetry-flusher.py` (long-lived background process; runs under `systemd --user` on Linux / WSL).
5. Write `scripts/telemetry-setup.sh` (idempotent: creates state dir, clones disler, starts Bun + Vite + flusher).
6. Write `scripts/telemetry-status.sh` (one-shot status print: flusher up/down, disler reachable, SQLite row count, last event time).
7. Write `scripts/telemetry-rotate.sh` (nightly rotation; or call from a cron / systemd timer).
8. Write `scripts/bench-hook.sh` and `scripts/check-bench-budget.py` (Section 7).
9. Write `scripts/analyze-tool-trace.py` with the per-tool aggregates + slowest 10 + per-prompt cost + agent productivity sections (Section 5).
10. Write `tests/telemetry/test_append_atomicity.py` (Section 3.7, MUST-04) and fixture set `tests/telemetry/fixtures/*.json`.
11. Smoke test: start a fresh Claude Code session with `DISLER_ENABLED=false`, run 5 simple tool calls, verify the SQLite row count increases, verify the redactor catches a planted test secret, verify the bench-hook.sh p99 is under 5 ms.
12. Stand up disler (run `scripts/telemetry-setup.sh`), flip `DISLER_ENABLED=true`, verify the dashboard renders the same events.
13. Iterate gitleaks usage as real traces reveal patterns the default rules miss (custom `.gitleaks.toml` per-project addition is the extension point).
14. Confirm `SubagentStart`/`SubagentStop`-based agent-productivity report against an actual subagent dispatch.

Estimated v1 build time: 6-8 hours (up from 2-3 in the pre-Gate-1 draft; the broader hook lifecycle, dual-sink writer, gitleaks integration, and disler setup script add scope; the design's foundations now compound well for v2).

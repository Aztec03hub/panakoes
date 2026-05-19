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
   7. Disler fork: pin + ownership
   8. Migration path
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

Hooks have a synchronous default, which would slow the orchestrator on every operation. We address this with an async-shim architecture (see Section 3.5) so the synchronous portion stays under 35 ms p99 warm (see Section 7; the original 5 ms target was found unmeetable in adversarial review and was relaxed with the jq-collapse mitigation applied in the shim).

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

   <!-- MUST-03 / ADV-HIGH-07: RESOLVED 2026-05-19 via integration test. Inner field is `input`. -->
   **MCP tool inputs are nested under `input` (RESOLVED 2026-05-19 empirically).** The integration test in `tests/telemetry/test_integration_end_to_end.py` confirmed `tool_input.input` is the canonical field (architect-reviewer RES-02 was correct; earlier draft references to `params` were wrong). The dual-walk fallback in the flusher's brief-extractor is retained as defense-in-depth (if Claude Code's hook payload format changes, the extractor still works) but `params` is dead in current Claude Code. The hook payload shape:

   ```jsonc
   {
     "tool_name": "mcp__plugin_github_github__create_pull_request",
     "tool_input": {
       "server": "github",
       "tool":   "create_pull_request",
       // Either "input": {...} or "params": {...}; walk both per the conservative rule below.
       "input":  { "title": "...", "body": "...", "head": "...", "base": "..." }
     }
   }
   ```

   **Resolution (RESOLVED 2026-05-19).** The brief-extractor reads `tool_input.input` (the verified canonical field) and falls back to `tool_input.params` if present (defensive against future Claude Code format changes), then to `tool_input` itself as a last resort. Prepend `<server>/<tool>: ` then `tostring(<inner-field>)`. The verified MCP fixture lives at `tests/telemetry/fixtures/mcp_create_pull_request.json`. The `params` branch is dead in current Claude Code; removing it in a follow-up PR is fine but the cost of carrying it (one conditional) is negligible.

2. **Redact via gitleaks-as-a-library** (IMP-04). We replace the hand-rolled 10-pattern `sed` set with `gitleaks detect --no-git`, which ships [160+ rules](https://github.com/gitleaks/gitleaks) maintained continuously. Output is JSON-structured and includes the rule name, so redacted markers become `<REDACTED:gitleaks:aws-access-token>` etc. Coverage we gain over the hand-rolled set: GitHub fine-grained PATs (`github_pat_<22>_<59>`), Stripe restricted keys (`rk_live_` / `rk_test_`), DigitalOcean, Cloudflare, Slack webhook, Twilio, SendGrid, MailGun, Datadog, PagerDuty, Linear, Notion, Figma, Vercel, npm token, pypi token, Docker Hub token, SSH private-key bodies (not just the BEGIN header), database connection strings, and generic high-entropy strings paired with surrounding-keyword context for far fewer false positives than naked regex. See [secret-scanner comparison](https://appsecsanta.com/sast-tools/gitleaks-vs-trufflehog).

   **Implementation note (2026-05-19):** the original design specified `gitleaks detect --pipe --redact` to consume stdin. In gitleaks 8.21.2 the `--pipe --redact` combination is broken - scans take ~7 seconds (not 50-100ms) AND many rules silently don't fire. The flusher in `scripts/telemetry-flusher.py` instead uses **file-based scanning with sentinel substitution**: write the batch to a temp file, run `gitleaks detect --no-git --source <tempfile> --report-format json --report-path <out>`, parse the JSON findings (which include the offset + rule), and post-process the original text to substitute `<REDACTED:gitleaks:RuleID>` at the reported offsets. Roughly 50-200ms per batch (well under the prior 7s broken-`--pipe` time). Acceptable to keep `--pipe --redact` in the design rhetorically for the conceptual model, but the actual implementation MUST use the file-based path until gitleaks fixes the `--pipe` regression.

   **Latency note:** gitleaks startup is ~50-100 ms cold, heavier than `sed`. Two mitigations: (a) the flusher runs gitleaks once per drained batch, not once per event, amortizing startup across N events; (b) the async-shim architecture in Section 3.5 absorbs the redaction cost entirely off the synchronous path, so the hook latency the orchestrator sees stays inside the 35 ms p99 warm budget regardless of redactor cost. The flusher's gitleaks subprocess can also be kept warm via a long-running daemon wrapper (FIFO + tight read loop) if batch-mode is not enough.

3. **Truncate after redaction** to a sensible cap. 80 chars is too little. Caps: **2048 chars** for `Bash` (commands can be long), **1024 chars** for `Agent` (descriptions plus subagent_type), **512 chars** for everything else. Append `...` if truncated.

4. **JSON-encode the brief** so newlines, quotes, control characters do not break the JSONL line.

<!-- ADV-CRIT-03: per-tool extraction at hook time keeps Section 8 no-response-body invariant intact -->
5. **Per-tool dedicated-column extraction** (PostToolUse only; runs alongside brief generation in the flusher; reads `tool_result.content` once and discards it, persisting only the extracted scalar so the Section 8 "we never store the response body" invariant remains true):
   - `Bash` where `tool_input.command` contains `gh pr create`: regex `pull/(\d+)` against `tool_result.content`; capture as `pr_number INTEGER` column on the events row.
   - `mcp__plugin_github_github__create_pull_request`: read `tool_result.number` (the MCP server returns a structured JSON body with `number` at the top level); capture as `pr_number INTEGER`.
   - Other tools: no per-column extraction; the `pr_number` column stays NULL.

   The extraction is the single read of `tool_result.content`; once `pr_number` (or NULL) is stamped on the row, the response body is not stored anywhere. Section 8 invariant holds.

### 3.5 Async shim hooks + background flusher <!-- IMP-06: all hooks run async -->

All 12 hooks run async (fire-and-forget background process). The shell hook command is now a tiny shim that writes the raw event JSON to a spool directory and returns within milliseconds; a separate background flusher process reads the spool, runs gitleaks redaction in batch, computes the brief, and dual-writes to the SQLite-WAL sink and the disler dashboard.

Spool layout:

```
${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry/spool/<session_id>/<tool_use_id>-<hook_event_name>.json
```

The shim:

```bash
#!/usr/bin/env bash
# trace-shim.sh: write raw event to spool, return in <35ms warm (Section 7 budget).
set -uo pipefail
LOGDIR="${PANAKOES_TELEMETRY_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry}"
SPOOL="$LOGDIR/spool"
mkdir -p "$SPOOL"

# Read stdin event verbatim; do not parse, do not redact.
event=$(cat)

# ADV-HIGH-04: collapse three jq invocations into one to reduce p99 latency.
# jq startup is ~5 ms; doing it three times is ~15 ms by itself.
# (The original 15 ms warm target was unmeetable on WSL2 + miniconda3 hardware
# in practice; relaxed to 35 ms p99 warm 2026-05-19 after empirical bench. See Section 7.)
# IMPL NOTE 2026-05-19: `IFS=$'\t' read` (as in earlier drafts) silently coalesces
# empty middle fields; use newline-separated parsing OR an explicit array unpack
# to preserve empty `tool_use_id` for non-tool events.
read session_id tool_use_id hook_event_name <<<"$(jq -r '[.session_id // "unknown", .tool_use_id // "", .hook_event_name // "unknown"] | @tsv' <<<"$event")"

# ADV-HIGH-02: use mktemp for collision-free filenames (date +%N is GNU-only and silent overwrite
# on same-nanosecond events would lose data). mktemp uses O_CREAT|O_EXCL semantics.
# Default the tool_use_id component to a per-process random when missing (covers SessionStart,
# SessionEnd, UserPromptSubmit, Stop, PreCompact, Notification, PermissionRequest).
mkdir -p "$SPOOL/$session_id"
id_part="${tool_use_id:-${hook_event_name}-$$-${RANDOM}}"
out="$(mktemp -p "$SPOOL/$session_id" "${id_part}-${hook_event_name}-XXXXXXXXXX.json")"
printf '%s\n' "$event" > "$out"
exit 0
```

The flusher (`scripts/telemetry-flusher.py`, runs as a long-lived background process under `systemd --user`; Linux/WSL2 only for v1 per ADV-HIGH-01): <!-- ADV-HIGH-01: drop LaunchAgent reference; macOS is v2 -->


1. Polls `$SPOOL/*/` every 250 ms with inotify fallback.
2. Drains all complete files into an in-memory batch.
3. Runs gitleaks on the batch (one subprocess invocation per batch, not per event).
4. Computes the brief per the rules in Section 3.4.
5. Inserts into the SQLite-WAL sink (Section 3.6).
6. POSTs to the disler dashboard at `$DISLER_URL/events` (Section 4).
7. Marks failures distinctly so a future backfill tool can replay disler-side gaps without re-running gitleaks.

The flusher's `disler_pushed_at` column starts NULL and is set on successful POST, so we can detect and (optionally) replay un-pushed events without losing the durable SQLite record. The hook itself never blocks on disler reachability.

<!-- ADV-CRIT-01: trace propagation mechanism is now spec'd end-to-end so trace_id/span_id/parent_span_id are not decorative. -->
#### Trace context propagation

The shim does NOT generate W3C trace IDs (it writes raw events verbatim, by design, to stay within the synchronous-hook budget). All trace plumbing happens in the flusher, in batch, in-process:

1. **SessionStart** event drains. The flusher generates a fresh `trace_id` (32 hex chars), persists it to `${SPOOL}/sessions/<session_id>/trace_id` (one-file-per-session), and stamps it on the SessionStart event before the SQLite INSERT. A `sessions` table (small companion table; one row per session) caches the same mapping for O(1) read on subsequent batches without re-reading the spool file. The trace_id assignment is idempotent (write only if the file does not exist) so a re-drain after crash never re-mints.
2. **Any subsequent event in the same session_id**: the flusher reads the cached `trace_id` (in-memory map keyed by `session_id`, hot path) and stamps it on the event. If the cache is cold (flusher restart), the flusher reads from the SQLite `sessions` table once and re-warms.
3. **`span_id` generation**: the flusher generates a fresh `span_id` (16 hex chars) per event, EXCEPT that PreToolUse and PostToolUse for the same `tool_use_id` share a span_id. The flusher maintains an in-memory `span_id_by_tool_use_id` map: PreToolUse INSERTs an entry, PostToolUse / PostToolUseFailure looks it up and reuses the value. The map entry is dropped after the matching Post* event arrives to bound memory. If a PostToolUse arrives without a preceding PreToolUse cache entry (flusher restart, race), the flusher generates a fresh span_id and accepts that the pre/post linkage is broken for that one tool call (logged to `flusher.log` for visibility, not a hard error).
4. **`parent_span_id` propagation**: the flusher maintains an in-memory `active_span_id_by_session` map. On PreToolUse and SubagentStart, the flusher reads the session's current active span (the parent), stamps it as `parent_span_id` on the new event, and updates the map to the new span_id (so the new span becomes the active parent for any nested calls). On PostToolUse / PostToolUseFailure / SubagentStop, the flusher pops the active span back to the prior parent. For SubagentStart specifically, the orchestrator's session_id and the subagent's session_id may differ; the flusher links via `agent_id` (the SubagentStart event carries the orchestrator's active span_id as `parent_span_id` so the cross-session linkage is preserved without a session_id join).

**Test that proves the propagation works:** `tests/telemetry/test_trace_propagation.py` plays back a synthesized event sequence (SessionStart, two PreToolUse/PostToolUse pairs, one SubagentStart, two PreToolUse/PostToolUse pairs inside the subagent context, one SubagentStop, one SessionEnd) through the flusher and asserts: (a) every event shares the same `trace_id`; (b) PreToolUse and PostToolUse for the same `tool_use_id` share `span_id`; (c) the `parent_span_id` chain forms a valid tree (every non-root span's parent is a previously-seen span_id); (d) the subagent's tool calls have `parent_span_id` pointing to the SubagentStart span. The test runs in CI on changes to `scripts/telemetry-flusher.py` or this design's Section 3.5.

**Why not generate in the shim:** generating trace IDs in the shim would either require the shim to read a session-state file (extra fork + extra read, busting the budget) or pass state via process environment (hooks run as independent processes, no shared parent). Keeping all propagation in the flusher means the shim stays a pure spool-writer and the flusher owns all correlation logic in one place.

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
-- ADV-HIGH-08: auto_vacuum = INCREMENTAL so rotation does not need an exclusive-lock VACUUM.
PRAGMA auto_vacuum  = INCREMENTAL;

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
  pr_number                   INTEGER,          -- ADV-CRIT-03: extracted at hook-time from `gh pr create` / MCP create_pull_request response; NULL for all other events.
  payload                     TEXT,             -- full event JSON, redacted, as a fallback / for ad-hoc query
  disler_pushed_at            TEXT,             -- ISO 8601 when POSTed; NULL if not yet / failed
  -- ADV-MED-02: synthetic dedup key for UNIQUE constraint; SQLite treats NULL as distinct in UNIQUE, so a STORED generated column with COALESCE on the nullable parts is needed.
  dedup_key                   TEXT GENERATED ALWAYS AS (session_id || '|' || COALESCE(tool_use_id, '') || '|' || hook_event_name) STORED,
  UNIQUE (dedup_key)
);

CREATE INDEX idx_events_session_id   ON events (session_id, timestamp);
CREATE INDEX idx_events_trace        ON events (trace_id, span_id);
CREATE INDEX idx_events_tool         ON events (tool_name, timestamp);
CREATE INDEX idx_events_agent        ON events (agent_id, timestamp);
CREATE INDEX idx_events_disler_null  ON events (disler_pushed_at) WHERE disler_pushed_at IS NULL;
```

W3C trace fields are first-class columns on our side (they fit the relational model and make the parent-child join cheap). On disler's side they live inside the `payload` blob because disler's schema does not have columns for them. See [SQLite WAL](https://sqlite.org/wal.html).

<!-- ADV-MED-02: dedup_key + INSERT OR IGNORE protects against double-ingestion from spool-restart races and backfill replays. -->
**Idempotent ingest:** The flusher uses `INSERT OR IGNORE` against the events table; SQLite's UNIQUE constraint on `dedup_key` makes re-insertion of the same `(session_id, tool_use_id, hook_event_name)` triple a silent no-op. This protects against two scenarios: (a) the flusher crashes after INSERT but before deleting the spool file, then drains the file again on restart; (b) a future backfill tool replays an event whose `disler_pushed_at` was NULL. The synthetic `dedup_key` is necessary because SQLite's UNIQUE semantics treat NULL as distinct (unlike standard SQL), so a multi-column `UNIQUE(session_id, tool_use_id, hook_event_name)` would let multiple `tool_use_id = NULL` rows through (every SessionStart, SessionEnd, UserPromptSubmit, Stop, PreCompact, Notification, PermissionRequest event), defeating the constraint for non-tool events. Caveat: this assumes `(session_id, tool_use_id, hook_event_name)` is uniquely identifying for an event; if a single `tool_use_id` ever has more than one PostToolUse (it should not by Claude Code hook semantics), revisit.

### 3.7 JSONL spool

The JSONL spool stays under `${XDG_STATE_HOME}/panakoes-telemetry/spool/` as the async intake buffer only. It is drained every 250 ms and never accumulates more than a few seconds of events. The analyzer does not read the spool directly; it reads the SQLite database. The spool exists to decouple the synchronous hook from the redaction + writer pipeline.

<!-- MUST-04 / ADV-HIGH-02: one-file-per-event semantics, not O_APPEND. -->
**One-file-per-event atomicity assertion:** The shim writes one file per event via `mktemp` (Section 3.5), which under POSIX semantics creates the file with `O_CREAT|O_EXCL` and a random suffix (`XXXXXXXXXX`). The file does not exist before `mktemp` succeeds; the file is unique by construction; the subsequent `printf '...' > "$out"` writes the JSON event to the freshly-created path. Cross-write interleaving is structurally impossible because no two events ever target the same path. The earlier draft's reliance on `O_APPEND` atomicity guarantees (per [POSIX](https://pubs.opengroup.org/onlinepubs/9699919799/functions/V2_chap02.html#tag_15_09_07) and [empirical kernel behavior](https://www.notthewizard.com/2014/06/17/are-files-appends-really-atomic/), writes of length `<= PIPE_BUF` (4 KB) through `O_APPEND` are atomic on POSIX local filesystems) does NOT apply to one-file-per-event; the relevant guarantee for our pattern is `O_CREAT|O_EXCL` filename uniqueness, which `mktemp` provides portably. We retain `>>` (with `O_APPEND`) for any internal log file the flusher itself writes (for instance, the flusher's own stderr log under `flusher.log`); for those long-lived shared logs the PIPE_BUF atomicity argument still applies (and Linux 4.2.6+ raised the practical atomicity ceiling on ext4 to 1 MB, but we do not rely on the higher bound).

**Test that proves the assertion:** `tests/telemetry/test_append_atomicity.py` spawns 64 concurrent writers, each issuing 10,000 1-KB lines through `>>` against a file on the configured `XDG_STATE_HOME` filesystem. After all writers exit, the test parses the file as JSONL and asserts every line is well-formed JSON with no interleaving artifacts (no `}{ `, no truncated string literals, no zero-length lines). The test runs in CI on the Linux runner (ext4) and is gated on `df -T "$XDG_STATE_HOME" | awk 'NR==2 {print $2}'` returning `ext4`, `btrfs`, `xfs`, or `tmpfs`. If the filesystem is `9p` / `nfs` / `cifs` / `smbfs`, the flusher fails fast at startup with an explicit error message; we do not attempt to support non-local filesystems.

**Startup filesystem check** (in the flusher init; Linux/WSL2 only per ADV-HIGH-01):

```bash
# ADV-HIGH-01: df -T is GNU-only (BSD df has no -T). Failing the check fast is the
# right v1 stance because macOS support is out of scope for v1 (see Section 10).
if ! fstype=$(df -T "$LOGDIR" 2>/dev/null | awk 'NR==2 {print $2}'); then
  echo "panakoes-telemetry: df -T failed (BSD df has no -T flag); v1 supports Linux/WSL2 only. Refusing to start." >&2
  exit 1
fi
case "$fstype" in
  ext4|btrfs|xfs|tmpfs) : ;;
  *) echo "panakoes-telemetry: $LOGDIR is on $fstype, which does not guarantee O_APPEND atomicity. Move to a local POSIX filesystem (ext4/btrfs/xfs/tmpfs). Refusing to start." >&2; exit 1 ;;
esac
```

This catches the case where Phil moves the project root to `/mnt/c/...` (9P) or mounts a network share for the state dir. macOS would also fail this check (BSD `df` rejects `-T`, then `apfs` would not be in the allowlist); macOS support is explicitly deferred per Section 10.

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

<!-- ADV-SET-C: forked-snapshot framing per Section 4.7 -->
We now run a Panakoes-owned fork of disler's project (URL and pinned SHA in Section 4.7); the fork is our version, with no automatic upstream tracking. We do NOT vendor disler's code into the Panakoes public MIT repo (the upstream's missing LICENSE file means default "all rights reserved" applies and forking does not grant a redistribution license). We treat the fork as a runtime dependency reached over HTTP: cloned to a local data dir by the setup script, run as a local Bun server, never committed to the Panakoes tree. Our hook capture, gitleaks redaction, async flusher, and SQLite-WAL sink are built in-tree and remain the source of truth; the fork is an addressable live-view sink that we now own enough to patch (see Section 4.7's patch backlog).

### 4.2 Architecture: dual-sink hook flusher

```
Claude Code hook fires
    |
    v
[ async shim writes raw event to spool/ ]   <-- <35ms p99 warm (Section 7)
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
    (both sinks see the SAME redacted event; redaction is the only defense
     against secret leakage to remote sinks)   <-- ADV-HIGH-03
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

<!-- ADV-HIGH-05: dual-write ordering + Idempotency-Key. -->
**Dual-write ordering (explicit).** The flusher writes in this order for each event:

1. INSERT the row into SQLite (the `disler_pushed_at` column defaults to NULL).
2. POST the projected payload to disler with `Idempotency-Key: <session_id>-<tool_use_id>-<hook_event_name>` header.
3. On HTTP 2xx, UPDATE the SQLite row to set `disler_pushed_at = <now()>`.

The SQLite INSERT commits before the POST is initiated. If the flusher crashes between INSERT and POST (or between POST and UPDATE), the SQLite row stays at `disler_pushed_at = NULL`, which the future backfill tool can detect and replay. The analytics-of-record never diverges from the POST log because the row is recorded BEFORE the network call.

**Idempotency-Key header.** We send `Idempotency-Key: <session_id>-<tool_use_id>-<hook_event_name>` (the same triple that forms our SQLite `dedup_key`). Disler may not honor the header (we own the fork now and could add header processing per Section 4.7's patch backlog, but the v1 design does not require it); residual risk is duplicate events on the live dashboard during replay/restart, which is acceptable because the live dashboard is best-effort and not analytics-of-record. The SQLite side is protected against duplicates by the UNIQUE(dedup_key) constraint (Section 3.6 / MED-02).

Disler server unreachable, slow, or returning 5xx:

- Log the failure to stderr (visible in the flusher's own log under `${XDG_STATE_HOME}/panakoes-telemetry/flusher.log`).
- DO NOT block the hook (the hook has already returned to the orchestrator).
- DO NOT spool retries to disler (disler is a live dashboard, missed live events are acceptable; SQLite has the durable record).
- Leave the affected SQLite rows with `disler_pushed_at = NULL` so a future backfill tool can scan the index and replay if Phil wants to.
- Health-check: the flusher pings the verified disler health endpoint every 60 s (see endpoint-verification note below; ADV-HIGH-06) and surfaces the up/down state via `scripts/telemetry-status.sh` (and a future local UI). A long-running outage produces one stderr line per minute, not one per event.

<!-- ADV-HIGH-06: disler /health endpoint not yet verified; document assumption + fallback. -->
**Health-check endpoint (assumption flagged).** ASSUMPTION: the disler Bun server exposes `GET /health` returning 2xx when up. This needs to be verified against the disler-fork source before the implementation PR ships. If `/health` does not exist, swap to `HEAD $DISLER_URL/events` (the POST endpoint we already rely on; HEAD on a POST-only endpoint typically returns 405 in the up-state, which is still a "service is reachable" signal distinct from network-level errors), or whatever route the disler server actually exposes. The flusher's health-check path is configurable via `DISLER_HEALTH_PATH` (default `/health`) to absorb this without a code change. Verification task is on the implementation PR's pre-merge checklist.

This is intentional asymmetry: we are willing to lose live-dashboard visibility under disler outages but we are not willing to lose analytics-of-record.

### 4.5 Operational setup

<!-- ADV-HIGH-03: v1 is localhost-only; EC2 / multi-machine deferred to v2 (Section 9). -->
Where disler's server runs:

- **Solo dev (v1 scope, the only supported configuration):** localhost, on Phil's box, Bun server on port 4000. `$DISLER_URL` is `http://localhost:4000`. The flusher refuses to start if `$DISLER_URL` resolves off-localhost in v1 (the strict-local check is overridable for testing with `PANAKOES_TELEMETRY_ALLOW_REMOTE=1`, but the v1 contract is local-only).
- **Multi-machine (deferred to v2):** see Section 9.

Environment variables:

- `DISLER_URL` (default `http://localhost:4000`): the dashboard server endpoint. Override for remote.
- `DISLER_ENABLED` (default `false` initially): explicit on-switch. Flip to `true` once the server is up. With `DISLER_ENABLED=false`, the flusher skips the POST step but still writes SQLite, so we can stand up the local sink before the dashboard.
- `PANAKOES_TELEMETRY_DIR` (default `${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry`): root of the spool, SQLite, archive, and disler-server checkout.

Setup command: `scripts/telemetry-setup.sh` does the following idempotently (v1 scope is intentionally narrow per ADV-HIGH-09; do less and let the prereqs the user already has do the work):

<!-- ADV-HIGH-09: prereq-check Bun rather than install it; drop Vite dev server entirely; do not auto-open browser. -->
1. Creates `${PANAKOES_TELEMETRY_DIR}` (state files: `spool/`, `archive/`) and the disler clone parent `${XDG_DATA_HOME:-$HOME/.local/share}/panakoes-telemetry/`.
2. Clones the Panakoes fork of disler into `${XDG_DATA_HOME:-$HOME/.local/share}/panakoes-telemetry/disler/` if not already present (fork URL in Section 4.7).
3. Optional `--update` flag fast-forwards the fork checkout to the current pinned commit (no implicit pulls; the pin is intentional per Section 4.7).
4. Checks that Bun is installed (`bun --version`). If absent, prints `please install bun via https://bun.sh/docs/installation and re-run` and exits 1.
5. Runs `bun install` in the disler checkout to populate node_modules.
6. Builds the dashboard with `bun run build` in the disler checkout (produces static assets; no separate Vite dev server).
7. Starts the Bun server on `$DISLER_PORT` (default 4000) as a `systemd --user` unit (Linux/WSL2 only per ADV-HIGH-01). The Bun server serves both the POST `/events` endpoint AND the built dashboard assets at `/` (single port; no separate Vite process).
8. Prints the dashboard URL to stdout (e.g. `dashboard available at http://localhost:4000/`); does NOT attempt to auto-open a browser (WSL2 + wslview + headless edge cases are not worth the script complexity for one-line manual paste).
9. Initializes the SQLite database with the schema in Section 3.6 if not present.
10. Starts the flusher daemon if not running.

The setup script is the ONLY place we touch the disler tree; we do not check the disler fork's code into our repo, and the fork checkout lives outside the Panakoes worktree.

### 4.6 License risk acknowledgment

As of 2026-05-18, [`https://github.com/disler/claude-code-hooks-multi-agent-observability/blob/main/LICENSE`](https://github.com/disler/claude-code-hooks-multi-agent-observability/blob/main/LICENSE) returns 404. Default GitHub state without a LICENSE file is "all rights reserved." We mitigate by NEVER vendoring disler's code into our public MIT repo; we run disler's server out-of-tree (cloned to our local data dir, see Section 4.7) and only invoke it over HTTP at runtime, which is well-established to not require a license grant. The forking step (Section 4.7) is allowed by GitHub's Terms of Service even without an explicit license; self-hosting the fork outside GitHub remains in the same legal-gray-zone as before. We accept the risk for personal / portfolio / non-redistributed use; if Panakoes ever monetizes a hosted offering that derivatively uses disler's code, we re-evaluate (either get disler's explicit grant or rewrite). Mitigation summary: forking + pinning + owning patch authority (Section 4.7) eliminates upstream-drift risk and gives us bug-fix latitude, but the underlying license posture is unchanged.

### 4.7 Disler fork: pin + ownership

<!-- ADV-SET-B: forked disler to Panakoes-owned GitHub; pin a known-good SHA; eliminate upstream-drift risk. -->
**Why fork rather than reach upstream over HTTP.** Cloning directly from `github.com/disler/...` exposes the design to upstream-drift risk: disler may push a breaking schema change tomorrow that breaks our payload mapping (Section 4.3), or push behavior the flusher's dual-write ordering (Section 4.4) was not designed against, or archive the repo, or change terms. Forking to a Panakoes-owned GitHub URL eliminates that risk and gains patch authority: we can fix any disler bug we find directly in our fork (see the patch backlog below), add HIGH-05's Idempotency-Key handling on the server side, add HIGH-06's `/health` endpoint if absent, and audit every upgrade deliberately rather than absorbing whatever upstream pushed. We pin to a known-good upstream SHA at fork time and treat the fork as our own complete separate version.

**Fork details.**

- **Fork URL:** [`https://github.com/Aztec03hub/claude-code-hooks-multi-agent-observability`](https://github.com/Aztec03hub/claude-code-hooks-multi-agent-observability)
- **Pinned upstream SHA at fork time:** `8a6e5cf795df50767cea7123703751282a819697`
- **Forked:** 2026-05-19 (commit history at fork-time is whatever the upstream pinned-SHA contained; subsequent commits on the fork are Panakoes-authored).
- **Maintenance posture:** treat as our own project. No automated upstream-tracking. No Renovate / Dependabot watching upstream. If upstream ships something we want (a useful UI improvement, a bug fix we have not yet hit), we manually cherry-pick. The default is "we do not follow upstream"; cherry-picks are explicit and rare.

**Setup script update (cross-reference Section 4.5).** The `git clone` in Section 4.5 step 2 points at the fork URL, not the upstream URL:

```bash
git clone https://github.com/Aztec03hub/claude-code-hooks-multi-agent-observability \
  "${XDG_DATA_HOME:-$HOME/.local/share}/panakoes-telemetry/disler/"
```

The setup script's `--update` flag fast-forwards the local checkout to the current fork HEAD (NOT upstream's HEAD); the absence of upstream tracking is intentional.

**License posture under fork (cross-reference Section 4.6).** GitHub's Terms of Service explicitly permit forking any public repository regardless of the upstream's license posture; forking does not grant us a license to redistribute or self-host the code outside GitHub. We use the fork the same way we would have used the upstream: clone to a local data dir, run the Bun server locally, never vendor into the Panakoes public MIT repo, never redistribute. The legal-gray-zone risk for self-hosting is unchanged by the fork (no explicit grant either way); the operational risk of unannounced upstream changes IS eliminated by the fork. Phil's acceptance of the v1 risk is the same as in Section 4.6.

**Disler patch backlog (we own the fork; these are candidates for the implementation PR or follow-ups; NOT design-required for v1).**

- Add `Idempotency-Key` header processing in the POST `/events` handler so duplicate events on flusher restart are deduped server-side (HIGH-05; mitigates duplicate dashboard rows under crash-restart).
- Add a `GET /health` endpoint returning 2xx-when-up (HIGH-06; lets the flusher's health-check use the canonical name without the `DISLER_HEALTH_PATH` override).
- Drop the unused `themes` / `theme_shares` / `theme_ratings` tables from disler's schema (carries UI-customization noise our payload mapping does not use; reduces schema surface and per-row overhead).
- Add first-class `trace_id` / `span_id` / `parent_span_id` columns to disler's events table so the W3C fields no longer have to live inside the `payload` blob on disler's side (parity with our Section 3.6 schema; removes the per-row JSON parse cost for live-dashboard span-tree views).

These are post-v1 enhancements; v1 ships against the as-forked fork without these patches. They are listed here so a future implementation PR has a known starting point.

### 4.8 Migration path

If we later replace the disler dashboard (build our own UI, point at Grafana, integrate Langfuse), the dual-sink architecture means we swap only the HTTP target in the flusher. Our SQLite-WAL data is unchanged; trace IDs, span IDs, OTel GenAI field names, and agent attribution all carry forward. The migration story is now simpler than it would have been pre-fork: we own the dashboard, so deprecating it is a one-side decision; there is no upstream dependency to coordinate. If we point the flusher at a different sink, we either retire our disler fork (mark archived in our GitHub) or keep it as a parallel local dashboard. Estimate: a few hours of work to point the flusher at a new HTTP sink, plus whatever the new dashboard's schema mapping needs. The OTel GenAI naming (IMP-03) makes any OTel-aware backend (Datadog, Grafana Loki / Tempo, Honeycomb, Langfuse) a near-zero-config target.

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
3. Read `pr_number` directly from the PostToolUse row of the `gh pr create` (Bash) or `mcp__plugin_github_github__create_pull_request` call. The PR number is pre-extracted at hook-time per Section 3.4 step 5 and stored on the events row; the analyzer does NOT re-read `tool_result.content` (it is not stored, per the Section 8 invariant). <!-- ADV-CRIT-03: read pre-extracted pr_number column; no reference to a "result payload" we no longer store. -->
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

Telemetry state lives under `${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry/`; the disler fork checkout lives under `${XDG_DATA_HOME:-$HOME/.local/share}/panakoes-telemetry/disler/` (source code, not state, per XDG spec):

```
${XDG_STATE_HOME:-$HOME/.local/state}/panakoes-telemetry/
├── telemetry.sqlite          # SQLite-WAL analytics-of-record (Section 3.6)
├── telemetry.sqlite-wal      # WAL journal (managed by SQLite)
├── telemetry.sqlite-shm      # Shared memory file (managed by SQLite)
├── spool/                    # Async-hook intake buffer (Section 3.7)
│   └── <session_id>/<id-part>-<hook_event_name>-XXXXXXXXXX.json   # one file per event (mktemp), drained every 250 ms
├── archive/                  # Rotated SQLite snapshots
│   └── 2026-05-18.sqlite.zst # zstd-compressed daily archives
├── sessions/                 # Per-session metadata (trace_id cache; see Section 3.5.1 trace propagation)
│   └── <session_id>/trace_id # one-line file holding the session's W3C trace_id
└── flusher.log               # Flusher's own stderr log

${XDG_DATA_HOME:-$HOME/.local/share}/panakoes-telemetry/
└── disler/                   # Cloned Panakoes fork of disler (source code; Section 4.7)
```

We use `$XDG_STATE_HOME` for state (not `/tmp`, which is wiped on reboot or WSL shutdown and has no rotation; and not `~/.config`, which per the XDG spec is for config, not state). The disler source checkout uses `$XDG_DATA_HOME` because per the XDG Base Directory Specification, source code is "user-specific data," not state. Override the state path via `$PANAKOES_TELEMETRY_DIR` for non-XDG layouts or alternate volumes.

### 6.2 Rotation policy

The SQLite database rotates when EITHER condition first becomes true:

- Size exceeds 1 GB on disk, OR
- Age of the oldest row exceeds 90 days.

Rotation procedure (run nightly by the flusher, or on demand via `scripts/telemetry-rotate.sh`):

1. `VACUUM INTO 'archive/${YYYY-MM-DD}.sqlite'` to write a defragmented snapshot atomically.
2. `zstd -19 -T0 'archive/${YYYY-MM-DD}.sqlite'` to compress (typical ratio ~10x for JSONL-shaped data).
3. `rm 'archive/${YYYY-MM-DD}.sqlite'` after compression succeeds.
<!-- ADV-CRIT-02: rotation SQL `t` -> `timestamp` (column name correctness) -->
<!-- ADV-HIGH-08: drop manual VACUUM; rely on auto_vacuum=INCREMENTAL (see Section 3.6 PRAGMAs) -->
4. `DELETE FROM events WHERE timestamp < datetime('now', '-90 days')` against the live database. (Column name is `timestamp`, not the pre-Gate-1.5 alias `t`; audit Section 5 and Section 6.2 for any other dropped-alias leftovers.)
5. Skip an explicit `VACUUM`; the schema's `PRAGMA auto_vacuum = INCREMENTAL` (Section 3.6) reclaims space incrementally without a long exclusive lock. Avoids the 30-60 s exclusive-lock stall that would otherwise block flusher INSERTs during rotation; the flusher's spool keeps draining.
6. `find archive/ -name '*.sqlite.zst' -mtime +365 -delete` to purge archives older than one year.

**Smoke test for the rotation script:** `tests/telemetry/test_rotate.py` runs `scripts/telemetry-rotate.sh` against a 100-row fixture database with a mix of recent and >90-day-old rows, asserts only the old rows are deleted, asserts the live database PRAGMAs survive the run, and asserts a compressed archive is created under `archive/`.

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

<!-- ADV-HIGH-04: 5 ms target was off by 4x; relaxed to 15 ms in original design; further relaxed to 35 ms 2026-05-19 after WSL2 empirical bench. -->
**Ceiling on the synchronous-hook portion: 35 ms p99 warm** (was 15 ms in the original design; relaxed 2026-05-19 after empirical bench on Phil's WSL2 + miniconda3 hardware). The original 5 ms target was off by an order of magnitude given the bash shim's per-invocation fork overhead: `cat` (~1 ms) + 3x `jq -r` (~5 ms each, ~15 ms aggregate) + 2x `mkdir -p` (~1 ms) + file create-and-write (~1 ms) + shell startup (~2 ms) totals ~20 ms p50 warm and 30+ ms p95 warm before any optimization. The first mitigation, already applied to the shim in Section 3.5, collapses the three jq calls into a single `jq -r '[...] | @tsv'` invocation parsed by `read`. Measured 2026-05-19 across 8 fixtures: p50 ~27 ms, p99 ~30 ms; even the Python-shim escape hatch (single Python process) measures 21 ms p99 due to interpreter startup. The 15 ms target was hardware-unmeetable; 35 ms p99 warm is the realistic ceiling with a small headroom for variance. Sub-15 ms would require a Rust-binary shim (sub-2 ms startup); deferred to v2 if-and-when actual orchestrator latency becomes a felt problem. The async background work (gitleaks redaction in batch, SQLite WAL insert, HTTP POST to disler) has no ceiling and runs in the flusher process.

**Escape hatch if 35 ms warm is unmeetable:** rewrite the shim in a single Python process. Cold startup is ~30 ms (Python interpreter import) but warm-cache latency was measured at ~21 ms 2026-05-19 (still over the original 15 ms target; eliminates per-fork jq compounding but Python import dominates). For sub-15 ms p99, the v2 candidate is a static Rust binary (sub-2 ms startup). Decision criterion: if real-session latency becomes a felt problem, build the Rust binary; otherwise the 35 ms p99 warm bash shim is good enough.

**Benchmark to enforce the budget:** `scripts/bench-hook.sh` runs the shim against a fixture set covering each tool's input shape, captures p50 / p95 / p99 / max wall-clock (warm-cache only; 10-iteration warmup before each fixture), and fails the budget gate if p99 exceeds 35 ms.

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
  --p99-ceiling-ms 35
```

The fixture set lives at `tests/telemetry/fixtures/*.json` and includes one fixture per tool (Bash, Edit, Read, Write, Grep, Glob, Agent, WebFetch, WebSearch, plus one representative `mcp__plugin_github_github__create_pull_request` and one `mcp__plugin_playwright_playwright__browser_navigate`). `check-bench-budget.py` aggregates results and exits non-zero if any tool's p99 exceeds the ceiling. The benchmark runs in CI on PR pushes that touch `.claude/hooks/**`.

**Note on the async-flusher's HTTP call to disler:** the POST to `$DISLER_URL` is unbounded in latency and explicitly does NOT contribute to the hook budget. The flusher absorbs it asynchronously off the hot path. A disler outage means slower batch drain (rows accumulate in spool/), not slower hook returns.

**Note on jq fork overhead:** per [jq performance discussion](https://news.ycombinator.com/item?id=24468874), jq startup is ~5 ms per invocation. The shim previously invoked jq three times to extract `session_id`, `tool_use_id`, and `hook_event_name`; the collapsed-jq form (`jq -r '[.session_id, .tool_use_id, .hook_event_name] | @tsv'` then `read session_id tool_use_id hook_event_name <<<"$line"`) is now the canonical shim per Section 3.5 (ADV-HIGH-04). The Python-shim escape hatch remains documented above if the collapsed-jq shim still misses budget on Phil's hardware.

## 8. Tradeoffs and open questions

| Concern | Resolution |
| --- | --- |
| Hook latency | Ceiling 35 ms p99 warm on the synchronous-hook portion (relaxed from the original 5 ms after adversarial review found the bash-shim fork overhead alone consumed ~20 ms); enforced by `scripts/bench-hook.sh` (Section 7). All redaction and writes happen in the async flusher. Python-shim escape hatch on file if the bench still misses budget. |
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
- <!-- ADV-HIGH-03: multi-machine disler (e.g. EC2) moved here from Section 4.5; needs hardening before re-enablement. --> **Multi-machine disler (EC2-hosted) and cross-machine correlation.** A small EC2 instance (t4g.nano, ~$5/month) that multiple Claude sessions can POST to would let Phil aggregate telemetry across boxes. Deferred to v2 because doing it safely requires: TLS-required transport (the flusher rejects non-HTTPS `DISLER_URL` unless an explicit insecure escape hatch is set); bearer-token auth on the disler endpoint (the flusher sends `Authorization: Bearer $DISLER_TOKEN`); network-ACL hardening (security group locking ingress to known egress IPs); and explicit operator acknowledgement that gitleaks-missed secrets in `tool_input` may leak over the wire. v1 stays localhost-only (Section 4.5) to keep scope bounded; each machine's SQLite database stays local.
- Auto-blocking on detected secrets in `tool_input` (the PreToolUse hook could reject a tool call whose input matches a high-confidence secret pattern). Useful but risky (false positives block real work).

## 10. Out of scope for v1

- Bash command sub-instrumentation (the wrapper-script-around-shell idea Phil floated). Revisit if Bash-tool brevity proves too coarse in practice.
- A full streaming pipeline beyond the disler dashboard. Disler covers the live-view need; a Grafana/Loki feed would be v3+.
- Modifying upstream disler. We own a fork of disler now (Section 4.7) and treat it as a separate Panakoes-owned project; "upstream" merges are not on the table by default.
- <!-- ADV-HIGH-01: macOS deferred to v2. --> macOS support. BSD `df` does not have `-T`, BSD `date` does not have `+%N`, LaunchAgent is a separate plumbing path, and Phil's day-to-day is WSL2. v1 is Linux/WSL2 only; macOS support is a v2 work item if Phil ever runs the telemetry on macOS (would require portable filesystem detection, portable nanosecond timestamps, and LaunchAgent unit templates).

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
11. Smoke test: start a fresh Claude Code session with `DISLER_ENABLED=false`, run 5 simple tool calls, verify the SQLite row count increases, verify the redactor catches a planted test secret, verify the bench-hook.sh p99 is under 35 ms warm (Section 7 ceiling per ADV-HIGH-04; if not, swap to the Python shim escape hatch and re-bench).
12. Stand up disler (run `scripts/telemetry-setup.sh`), flip `DISLER_ENABLED=true`, verify the dashboard renders the same events.
13. Iterate gitleaks usage as real traces reveal patterns the default rules miss (custom `.gitleaks.toml` per-project addition is the extension point).
14. Confirm `SubagentStart`/`SubagentStop`-based agent-productivity report against an actual subagent dispatch.

Estimated v1 build time: 8-12 hours (up from 6-8 in the pre-Gate-2 draft per ADV-HIGH-09; the trace-propagation mechanism (Section 3.5.1 / CRIT-01), the dedup-key + INSERT OR IGNORE + pr_number column wiring (CRIT-03 + MED-02), and the test surface (`test_trace_propagation.py`, `test_rotate.py`, the MCP fixture) absorbed the savings from setup-script scope cuts; the design's foundations still compound well for v2).

# Tool-trace telemetry: design

**Status:** Designed 2026-05-18, awaiting implementation
**Owner:** Phil + Claude orchestrator
**Triggered by:** Phil's observability ask during the 2026-05-18 marathon session
**Goal:** Per-tool timing + brief tool context + aggregated analytics, with rudimentary secret redaction so the trace is safe to keep on disk.

## Why this exists

We need observability into what the Claude orchestrator does, how long each tool call takes, and how that aggregates into higher-order metrics like "time from agent dispatch to PR merge" and "LOC shipped per agent-minute." The 2026-05-18 session produced ~20 merged PRs but the orchestrator had zero structured visibility into where its time went, which tool calls were slow, or which agent dispatches were most productive. Adding that visibility lets us:

1. Identify slow tool patterns to optimize (e.g. a sequence of 12 short `Read` calls that could be one `Grep`).
2. Compare orchestrator-direct work vs sub-agent dispatch ROI.
3. Surface the "agent dispatched at T, PR opened at T+N, PR merged at T+M, with K lines changed" arc as a reusable productivity baseline.
4. Catch regressions in tool performance (a tool that suddenly averages 3x slower is a signal worth investigating).

## What hooks give us (verified, not assumed)

`PreToolUse` and `PostToolUse` hooks receive a JSON event on stdin with at least:

- `session_id` (UUID for the conversation)
- `tool_name` (e.g. `Bash`, `Edit`, `Agent`, `mcp__plugin_github_github__create_pull_request`)
- `tool_input` (the tool's argument object: `command`, `file_path`, `prompt`, etc.)
- `tool_use_id` (unique per call; same value appears in both Pre and Post events for the same invocation, which is how we pair them)
- `tool_response` (PostToolUse only: the tool's output / error, often truncated by the hook framework)

Hooks run synchronously per tool call. A slow hook slows ME down on every operation. The budget per hook should be <50ms.

A Bash wrapper around the actual shell would let us instrument shell commands *inside* my `Bash` tool calls (one level deeper). Out of scope for v1; revisit if Bash-tool brevity turns out to lose too much detail.

## Architecture

### Two thin shell hooks, one JSONL log per session

```
.claude/hooks/trace-pre.sh    (PreToolUse,  matcher = "*")
.claude/hooks/trace-post.sh   (PostToolUse, matcher = "*")

→ append to /tmp/claude-tool-trace-<session_id>.jsonl
```

Each hook does the minimum work necessary: parse the stdin JSON, extract the fields, redact secrets in the brief, append one JSON line to the session log, exit 0. Append-only writes under 4 KB are atomic on POSIX kernels, so concurrent tool calls do not interleave.

### JSONL event format

```jsonl
{"t":"2026-05-18T23:55:11.123Z","ev":"pre","sid":"abc-uuid","seq":"toolu_01XYZ","tool":"Bash","brief":"git push -u origin chore/deps-typescript-6-admin"}
{"t":"2026-05-18T23:55:11.789Z","ev":"post","sid":"abc-uuid","seq":"toolu_01XYZ","tool":"Bash","success":true,"out_len":234,"err_len":0}
```

Pair pre and post via `seq` (the `tool_use_id`). Duration = `post.t - pre.t`.

### Brief field: expanded with redaction

The brief is the orchestrator's "what was this call about" summary. The earlier proposal capped it at 80 chars to minimize secret exposure. Phil's feedback: **expand it for richer context, but add rudimentary secret detection so we get the best of both.**

**Brief generation rules (in priority order):**

1. **Per-tool field selection** (extract the most informative field, skip the noise):
   - `Bash` → `command` (full, before redaction)
   - `Edit` / `Write` / `Read` → `file_path` (always safe, never contains secrets)
   - `Agent` → `description` + `subagent_type` joined with `/`
   - `Grep` → `pattern` + `path`
   - `Glob` → `pattern`
   - `WebFetch` / `WebSearch` → `url` or `query`
   - `mcp__plugin_github_github__*` → first 256 chars of the most-meaningful field per tool
   - default → first 256 chars of `JSON.stringify(tool_input)`

2. **Redact in-place** (regex-based, fast, conservative -- false positives over false negatives):

   | Pattern | Regex | Replacement |
   |---|---|---|
   | AWS access key | `AKIA[0-9A-Z]{16}` | `<REDACTED:aws-access-key>` |
   | AWS secret key | `(?<=[\W^])[A-Za-z0-9/+=]{40}(?=[\W$])` | `<REDACTED:aws-secret-candidate>` (lots of false positives; OK for redaction) |
   | GitHub PAT | `gh[opsu]_[A-Za-z0-9_]{36,255}` | `<REDACTED:github-pat>` |
   | Stripe live key | `sk_live_[A-Za-z0-9]{24,}` | `<REDACTED:stripe-live-key>` |
   | Stripe test key | `sk_test_[A-Za-z0-9]{24,}` | `<REDACTED:stripe-test-key>` (catches the placeholder pattern that bit us earlier) |
   | JWT | `eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+` | `<REDACTED:jwt>` |
   | Anthropic key | `sk-ant-[A-Za-z0-9_-]{20,}` | `<REDACTED:anthropic-key>` |
   | OpenAI key | `sk-(proj-)?[A-Za-z0-9_-]{32,}` | `<REDACTED:openai-key>` |
   | Private key header | `-----BEGIN [A-Z ]*PRIVATE KEY-----` | `<REDACTED:private-key-header>` |
   | Bearer token | `Bearer [A-Za-z0-9_.-]{20,}` | `Bearer <REDACTED:bearer-token>` |
   | URL with embedded creds | `https?://[^:/@\s]+:[^@\s]+@` | `<REDACTED:url-with-creds>://` |

   These cover the failure modes we have already seen in this session (gh-token-in-URL, Stripe placeholder). Add more as discovered.

3. **Truncate after redaction** to a sensible cap. Phil's note: 80 chars is too little. Proposed cap: **2048 chars** for `Bash` (commands can be long), **1024 chars** for `Agent` (descriptions plus subagent_type), **512 chars** for everything else. Append `...` if truncated.

4. **JSON-encode the brief** so newlines, quotes, control characters do not break the JSONL line.

### Hook script: `.claude/hooks/trace-pre.sh`

```bash
#!/usr/bin/env bash
# trace-pre.sh: append one JSONL "pre" event per tool call. Never blocks.
set -uo pipefail

LOGDIR="${CLAUDE_TRACE_DIR:-/tmp}"

# Parse event JSON from stdin
json=$(cat)
ts=$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)
tool=$(jq -r '.tool_name // "?"' <<<"$json")
sid=$(jq -r '.session_id // "unknown"' <<<"$json")
seq=$(jq -r '.tool_use_id // "?"' <<<"$json")

# Per-tool brief extraction (see Brief Generation Rules)
brief=$(jq -r --arg tool "$tool" '
  .tool_input as $i |
  if   $tool == "Bash"    then ($i.command // "")
  elif $tool == "Edit" or $tool == "Write" or $tool == "Read" then ($i.file_path // "")
  elif $tool == "Agent"   then ((($i.description // "") + " / " + ($i.subagent_type // "?")))
  elif $tool == "Grep"    then (($i.pattern // "") + " in " + ($i.path // "."))
  elif $tool == "Glob"    then ($i.pattern // "")
  elif $tool == "WebFetch" or $tool == "WebSearch" then ($i.url // $i.query // "")
  elif ($tool | startswith("mcp__")) then ($i | tostring)
  else ($i | tostring)
  end
' <<<"$json")

# Per-tool cap
case "$tool" in
  Bash)  cap=2048 ;;
  Agent) cap=1024 ;;
  *)     cap=512  ;;
esac

# Redact secrets (call the shared redactor)
brief=$(printf '%s' "$brief" | "$(dirname "$0")/redact-secrets.sh")

# Truncate after redaction
if [ ${#brief} -gt $cap ]; then
  brief="${brief:0:$cap}..."
fi

# Append JSONL line atomically (single write under 4KB is atomic on POSIX)
{
  jq -nc \
    --arg t "$ts" --arg sid "$sid" --arg seq "$seq" \
    --arg tool "$tool" --arg brief "$brief" \
    '{t:$t,ev:"pre",sid:$sid,seq:$seq,tool:$tool,brief:$brief}' \
    >> "$LOGDIR/claude-tool-trace-$sid.jsonl"
} || true

exit 0
```

### Hook script: `.claude/hooks/trace-post.sh`

Mirror of `trace-pre.sh`, but reads `tool_response`, derives `success`, and includes `out_len` / `err_len`. Same JSONL append pattern, same redaction on any captured response fragments.

```bash
# Key differences from pre:
tool_response_json=$(jq -c '.tool_response // {}' <<<"$json")
success=$(jq -r '.tool_response.is_error // false | not | tostring' <<<"$json")
out_len=$(jq -r '.tool_response.output // .tool_response.text // "" | length' <<<"$json")

# Emit:
# {"t":..., "ev":"post", "sid":..., "seq":..., "tool":..., "success":true/false, "out_len":N}
```

We deliberately do NOT log `tool_response.output` content. The output of a `Bash` call to read a secrets file is the kind of thing we never want to keep on disk. Only the length is recorded.

### Shared redactor: `.claude/hooks/redact-secrets.sh`

```bash
#!/usr/bin/env bash
# redact-secrets.sh: stdin → stdout, with high-confidence secrets replaced
# with <REDACTED:type> markers. Single sed pipeline for speed.
sed -E \
  -e 's|AKIA[0-9A-Z]{16}|<REDACTED:aws-access-key>|g' \
  -e 's|gh[opsu]_[A-Za-z0-9_]{36,255}|<REDACTED:github-pat>|g' \
  -e 's|sk_live_[A-Za-z0-9]{24,}|<REDACTED:stripe-live-key>|g' \
  -e 's|sk_test_[A-Za-z0-9]{24,}|<REDACTED:stripe-test-key>|g' \
  -e 's|eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|<REDACTED:jwt>|g' \
  -e 's|sk-ant-[A-Za-z0-9_-]{20,}|<REDACTED:anthropic-key>|g' \
  -e 's|sk-(proj-)?[A-Za-z0-9_-]{32,}|<REDACTED:openai-key>|g' \
  -e 's|-----BEGIN [A-Z ]*PRIVATE KEY-----|<REDACTED:private-key-header>|g' \
  -e 's|Bearer [A-Za-z0-9_.-]{20,}|Bearer <REDACTED:bearer-token>|g' \
  -e 's|(https?://)[^:/@[:space:]]+:[^@[:space:]]+@|\1<REDACTED:url-with-creds>@|g'
```

`sed` in one pass is fast (low ms). Extend the rule set as new secret formats surface in real traces.

### Hook registration: `.claude/settings.json`

```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-pre.sh" }] }
    ],
    "PostToolUse": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "${CLAUDE_PROJECT_DIR}/.claude/hooks/trace-post.sh" }] }
    ]
  }
}
```

Matcher `*` catches every tool. If we want to exclude certain tools (e.g. don't trace TaskList because it is constantly polled), use a regex like `^(?!TaskList|TaskGet$).+`.

## Post-processing: `scripts/analyze-tool-trace.py`

Run on demand or via cron. Reads one or more JSONL files, joins pre+post by `seq`, computes:

### Per-session summary
- Total tool calls
- Total wall-clock duration of all tool calls (sum of post.t - pre.t)
- Per-tool call count + p50 + p95 + max duration
- Top 10 slowest individual calls (tool, brief, duration)
- Error rate per tool (count where success=false / total)

### Agent lifecycle correlation (the headline metric)
For each `Agent` tool call:
1. Find pre.t and post.t (dispatch and completion times)
2. Scan post-Agent events in same session for `mcp__plugin_github_github__create_pull_request` or `Bash` calls containing `gh pr create`; extract PR number from response
3. Query `gh pr view <N> --json mergedAt,additions,deletions` (cached so we are not hammering the API across re-runs)
4. Compute: `agent_duration_ms`, `dispatch_to_pr_open_ms`, `pr_open_to_merge_ms`, `loc_added`, `loc_removed`, `loc_per_agent_minute`

### Weekly markdown report

`scripts/analyze-tool-trace.py --week 2026-W20 --out docs/analytics/2026-W20.md` produces:

```markdown
# Tool trace analytics: 2026-W20

## Summary
- Sessions: 12
- Total tool calls: 3,847
- Wall-clock tool time: 18h 24m
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
```

## Tradeoffs and open questions

| Concern | Resolution |
| --- | --- |
| Hook latency | Bash + jq + sed pipeline is <20 ms in benchmarks. Well under the 50 ms budget. |
| Secret detection false positives | Acceptable. Better to redact a non-secret than to leak one. The brief is for telemetry, not for reading back the exact command. |
| Secret detection false negatives | Real risk. Mitigations: (a) the redactor is extensible, add patterns as we learn; (b) the log file is gitignored; (c) future enhancement: nightly cron that runs gitleaks on the day's trace file and quarantines anything that scans positive. |
| Per-tool brief cap (2048 / 1024 / 512) | Subject to revision once real traces show the distribution of brief lengths. Cheap to tune. |
| Storage growth | A heavy session is ~10K events × ~300 bytes = ~3 MB. Negligible. Monthly cron rotates trace files older than 90 days to a compressed archive. |
| Hook failure blast | All hooks exit 0 unconditionally; the JSON write is wrapped in `{ ... } || true`. If a hook script disappears entirely, Claude Code logs a warning but does not block the tool call. |
| Sensitive `tool_response.output` | We log `out_len` only, never the response body. The body might contain anything (secrets file contents, AWS keys printed during debugging, etc.). |
| Privacy when sharing traces | A trace file should never be shared without first re-running the redactor on every brief AND a separate human eyeball pass. Document this on top of the analytics report. |

## Out of scope for v1

- Bash command sub-instrumentation (the wrapper-script-around-shell idea Phil floated). Revisit if Bash-tool brevity proves too coarse in practice.
- Real-time dashboards. The post-processing script is run on demand. A Grafana/Loki feed would be v3+.
- Cross-machine correlation. Each machine's traces stay local; we only aggregate within a single environment.
- Auto-blocking on detected secrets in tool_input. The PreToolUse hook could *reject* a tool call whose input matches a high-confidence secret pattern. Useful but risky (false positives block real work). Future enhancement.

## Implementation plan (next session)

1. Create `.claude/hooks/trace-pre.sh`, `trace-post.sh`, `redact-secrets.sh`. Make executable.
2. Add `.claude/settings.json` (or extend existing one) with the two hook registrations.
3. Add `/tmp/claude-tool-trace-*.jsonl` to `.gitignore` defense in depth.
4. Write `scripts/analyze-tool-trace.py` with a minimal "per-tool aggregates + slowest 10" report.
5. Smoke test: start a fresh session, run 5 simple tool calls, verify the JSONL file populates correctly and the redactor catches a planted test secret.
6. Iterate the redactor regex set as real traces reveal patterns the v1 list misses.
7. Add agent lifecycle correlation + weekly report once we have a week of trace data.

Estimated v1 build time: 2-3 hours. Estimated session ROI: by the second week of use, the orchestrator should be making measurably better dispatch decisions based on the productivity data.

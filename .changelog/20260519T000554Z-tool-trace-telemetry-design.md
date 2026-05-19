---
category: Added
---

- `docs/design/tool-trace-telemetry.md`: design doc for the tool-trace telemetry system. PreToolUse + PostToolUse hooks emit per-call JSONL events with ISO-8601 millisecond timestamps; pairs match via `tool_use_id`. Per-tool brief extraction (Bash command, Edit file_path, Agent description, etc.) with size caps 2048/1024/512 chars by tool class, run through a regex-based secret redactor (AWS keys, GitHub PATs, Stripe keys, JWTs, Anthropic/OpenAI keys, private-key headers, bearer tokens, URL-embedded creds). Post-processing computes per-tool aggregates (p50/p95/max), agent lifecycle correlation (dispatch to PR-open to PR-merge), and LOC-per-agent-minute productivity. Implementation deferred to next session per Phil's request; v1 estimated 2-3 hours.

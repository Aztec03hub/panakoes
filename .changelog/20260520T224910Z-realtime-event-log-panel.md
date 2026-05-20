---
category: Added
---

- `services/admin/realtime`: in-page event log panel below the transcript card showing a live timeline of session state transitions, WebSocket message receipts, mic lifecycle, and errors. Each row shows `HH:MM:SS.mmm | level | source | message` with color-coded levels (info / warn / error) and stable source tags (`session` / `ws` / `mic` / `catchup`). Smart auto-scroll preserves the user's scroll position when reading older entries and surfaces a "Jump to latest" affordance; collapsible header; capped at 500 entries (oldest dropped). The JWT in the WebSocket URL is redacted before it lands in the log.

---
category: Changed
---

- `services/transcriber-stream`: stage4 diagnostic logs now inline torch / ctranslate2 / model-dir state into the log message via f-strings, since the default stdlib logging formatter drops the `extra={...}` payload before it reaches CloudWatch.

---
category: Fixed
---

- `services/query-api`: `IngestionRecord` Pydantic model now includes `transcript_status`, `transcript`, and `transcript_error_message` fields. The transcribe-worker / transcriber-batch container writes these to the ingestion DDB row, but the previous query-api model dropped them on serialization (`extra="ignore"` + missing field declarations), so `GET /ingestions/{id}` returned the row without the transcript even when the row was fully transcribed. SPA `/ingestion/[id]` polling now renders the transcript end-to-end.

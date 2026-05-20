---
category: Added
---

- `services/admin`: new `/realtime` route for chunked-batch pseudo-realtime transcription. Records audio continuously, rotates the MediaRecorder every 8 seconds, and routes each slice through the existing async Whisper-on-Batch path (createIngestion, S3 PUT, transcribe-worker, query-api). Displays per-chunk and combined transcripts as they materialize.

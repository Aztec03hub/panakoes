---
category: Changed
---

- `services/admin`: split recording state from session state on `/realtime`. The big mic button now toggles recording (pause / resume) without tearing down the WebSocket or the GPU; a separate "End session" button closes the pipeline. Captured PCM is retained as a WAV blob so the user can play back the clip locally between recordings.

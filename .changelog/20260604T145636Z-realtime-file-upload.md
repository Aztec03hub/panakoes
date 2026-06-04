---
category: Added
---

- `services/admin`: the /realtime page can now transcribe an uploaded audio file through the live streaming GPU pipeline. Pick a file, and the SPA decodes it, resamples to 16 kHz mono, and streams it as the same 200 ms PCM frames the microphone emits, with per-frame progress, a 10 s drain window for trailing finals, and a clean graceful session end.

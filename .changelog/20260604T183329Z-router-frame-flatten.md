---
category: Fixed
---

- `services/streaming-router`: audio frames are flattened to the queue-message top level; the consumer reads `pcm_b64` there and was dropping every nested frame, so no live session ever produced a transcript.

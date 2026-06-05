---
category: Fixed
---

- `services/transcriber-stream`: audio frames are reassembled in `seq` order before inference; the standard-SQS frame queues deliver unordered/at-least-once and arrival-order feeding shredded the audio into word salad.

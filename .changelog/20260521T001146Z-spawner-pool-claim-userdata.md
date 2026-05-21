---
category: Fixed
---

- `services/gpu-spawner`: spawn callback now claims a pool queue, writes `frame_queue_url` to the streaming-session row, and emits a UserData script that actually pulls + runs the transcriber-stream container with all 8 required env vars. Stage 2's spawner only fired `systemctl enable --now` against a non-existent systemd unit, and never claimed a pool slot, so audio frames hit the floor in the streaming-router silent-drop path.

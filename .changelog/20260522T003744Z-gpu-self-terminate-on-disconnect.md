---
category: Added
---

- `services/transcriber-stream` + `infra/iam`: GPU EC2 self-terminate on graceful session end. The transcriber-stream container's `_drain_and_exit` (the path the `LifecycleWatcher` triggers when it sees `status=disconnected` on the session row) now calls `ec2:TerminateInstances` on its own instance after writing the final transcript + `ended` event. Stops the ~$0.526/hr per-aborted-session leak that previously waited for the next session's LRU-evict or a manual cleanup. IAM grant on `panakoes-dev-gpu-instance` is scoped to instances tagged `Project=panakoes` AND `Spawner=panakoes-dev-gpu-spawner`. Failures are swallowed; the LRU-evict from the previous PR remains the backstop for any crash that skips this path.

---
category: Added
---

- `services/admin`: SPA-side stall watchdog. When the streaming session enters `spawning-gpu`, the SPA arms a 90 s timer that resets on every inbound WS message. If it fires (e.g., spawn queue was purged, spawner ECS task crashed, EC2 cloud-init died before its first `post_status` call, or DLQ exhaustion), the session log gets a `warn` entry pointing to CloudWatch and an `isSpawnStuck` flag is exposed for UI surfaces. Backstops the server-side status events that the previous PR added: they cover the happy path + spawn-callback exceptions; the watchdog catches the silent failure modes the server cannot self-report.

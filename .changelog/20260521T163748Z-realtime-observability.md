---
category: Added
---

- `/realtime`: real-time visibility into the spawn and container init pipeline. The session log panel now receives status events from the streaming-router (`router-accepted`), the gpu-spawner (`spawn-message-received`, `pool-claimed`, `session-row-updated`, `run-instances-issued`, `instance-launching`, `spawn-failed`), the EC2 cloud-init script (`ec2-ecr-login`, `ec2-image-pull-start`, `ec2-image-pull-done`, `ec2-prewarm-start`, `ec2-prewarm-done`, `ec2-container-launched`), and the transcriber-stream container (`container-started`, `cuda-checked`, `model-loading`, `model-loaded`, `prompt-seed-read`, `warmup-complete`, `ready`). What was a 7+ minute black hole between "Status: connecting -> spawning-gpu" and "ready" is now ~15 events with timing.

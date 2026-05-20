---
category: Changed
---

- `infra/dev/ecs`: bump `gpu_spawner_image_tag` to `main-a926fba` (PR #460's fix for the EventBridge consumer's `GpuInstanceManager(settings=...)` TypeError; container now starts cleanly under FastAPI lifespan).

---
category: Fixed
---

- `infra/dev/ecs`: pin `gpu_spawner_image_tag` default from `initial` (placeholder, never baked) to `main-1784c92` (the merged PR #454 image with the EventBridge consumer wired into FastAPI's lifespan). ECS service `panakoes-dev-gpu-spawner` was unable to pull `:initial` and stuck retrying; this unblocks the scale-up.

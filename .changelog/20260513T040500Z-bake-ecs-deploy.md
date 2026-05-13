---
category: Added
---

- `.github/workflows/image-bake-on-change.yml`: added `deploy` job that runs after each successful image bake. Fetches the current ECS task definition, swaps the container image tag to the freshly baked `main-<sha7>` image, registers a new task definition revision, and calls `aws ecs update-service`. Services not yet deployed to ECS are skipped gracefully (no-op exit 0). Eliminates the manual deploy loop after every code push to main.

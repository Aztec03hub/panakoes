---
category: Fixed
---

- `services/auth/Dockerfile`: prefix all `COPY` source paths with `services/auth/` so the build works from repo-root context, matching every other service Dockerfile. Without this, the GHA image-bake hits `failed to compute cache key: "/drizzle": not found` and no new auth image gets baked.

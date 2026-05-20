---
category: Fixed
---

- `services/transcriber-stream/Dockerfile`: fix COPY paths to be repo-root-relative so the GHA image-bake workflow can build the image. The original Dockerfile assumed the build context was `services/transcriber-stream/` (which is how local `docker build` worked) but the bake workflow sets `context: .` (repo root); all COPY paths now use the `services/transcriber-stream/...` prefix to match every other service Dockerfile in the repo.

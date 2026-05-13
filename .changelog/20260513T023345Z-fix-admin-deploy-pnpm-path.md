---
category: Fixed
---

- `admin-deploy`: fix pnpm lockfile path and install working directory; the monorepo has per-service lockfiles under `services/admin/`, not at the repo root -- point `cache-dependency-path`, `pnpm install`, and the `pnpm build` invocation in `deploy-admin-spa.sh` at `services/admin/`.

---
category: Security
---

- `services/admin`, `services/otel-lib-ts`: pin transitive `protobufjs` to `^8.0.2` via pnpm `overrides` to clear 8 high-severity Dependabot alerts (GHSA-66ff-xgx4-vchm, GHSA-75px-5xx7-5xc7, GHSA-jvwf-75h9-cwgg, GHSA-685m-2w69-288q).

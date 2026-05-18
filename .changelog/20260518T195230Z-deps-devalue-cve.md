---
category: Security
---

- `services/admin`: bump `devalue` from `5.8.0` to `5.8.1` (pnpm override) to resolve CVE-2026-42570 (HIGH, DoS via sparse array deserialization). The package is transitive via `@sveltejs/kit`; the override pins it at the workspace level. Clears the recurring Trivy `Scan filesystem` CI failure.

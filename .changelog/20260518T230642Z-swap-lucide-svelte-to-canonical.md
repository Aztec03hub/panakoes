---
category: Changed
---

- `services/admin`: swap deprecated `lucide-svelte@0.460.1` for the canonical `@lucide/svelte` (latest, ^0.555.0). The old `lucide-svelte` package is deprecated upstream per its v1.0 release notes; `@lucide/svelte` is the maintained replacement and follows the per-icon import convention (`import Loader2 from "@lucide/svelte/icons/loader-2"`). Single usage in the codebase (`services/admin/src/routes/login/+page.svelte`); typecheck clean, 130/130 vitest pass. Supersedes the open Dependabot PR #381 (which would have bumped to `lucide-svelte@1.0.1`, the deprecation tombstone version).

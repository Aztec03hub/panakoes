### Added

- `services/admin`: Playwright e2e test harness (`@playwright/test` dev dep,
  `playwright.config.ts`, `tests/e2e/smoke.spec.ts`) so the svelte-worker agent
  can capture visual-verification screenshots after Svelte changes and so
  proper e2e suites can be authored against the admin SPA. `test:e2e` and
  `test:e2e:ui` scripts added. Artifacts land in `.playwright-artifacts/admin/`
  (already gitignored at repo root).

---
category: Changed
---

- `services/admin`: migrate to Tailwind CSS v4 (3.4.15 -> 4.3.0) and tailwind-merge v3 (2.5.5 -> 3.6.0). Codemod-driven: new `@tailwindcss/postcss` PostCSS plugin replaces `tailwindcss` + `autoprefixer`, `app.css` now uses `@import 'tailwindcss'` with `@config` linking the existing `tailwind.config.ts` (CSS-first config deferred), and 6 components migrated to v4 utility renames (`outline-none` -> `outline-hidden`, `shadow-sm` -> `shadow-xs`, `[&:has(...)]:` -> `has-[...]:`). Default v3 border color preserved via `@layer base` compat block. Typecheck clean (4089 files), 130/130 vitest pass, production build clean. Absorbs Dependabot PRs #383 and #384.

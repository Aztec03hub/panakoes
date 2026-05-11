# Dependabot Major-Bump Triage 2026-05-11

Triage of the 14 open Dependabot PRs (#248 through #261) against the Panakoes monorepo.

## Summary Counts

- **SAFE (auto-approved and queued for squash-merge):** 2
- **RISKY-MAJOR (flagged for Phil to review manually):** 10
- **DEFER (waits for prerequisite PR to land first):** 2

The SAFE PRs were auto-approved and set to squash-merge once CI passes. Per project discipline (`feedback_panakoes_lessons.md`), no major-version Dependabot PR is merged by Claude; each RISKY PR has a comment summarizing breaking-change expectations so future-Phil can act.

## Classification Table

| PR  | Package | From → To | Service | Class | Notes / Recommendation |
| --- | --- | --- | --- | --- | --- |
| #257 | admin-minor-patch group (OTel ×6, vitest, coverage-v8) | OTel 2.0.1→2.7.1 (minor); vitest 4.1.5→4.1.6 (patch) | services/admin | SAFE | Group bump, all minor/patch. Auto-approved + squash-merge queued. |
| #249 | auth-minor-patch group (better-auth, biome) | better-auth 1.6.9→1.6.10; biome 2.4.14→2.4.15 | services/auth | SAFE | Group bump, both patch. Auto-approved + squash-merge queued. |
| #248 | @types/node | 22.10.1 → 25.6.2 | services/otel-lib-ts | RISKY-MAJOR | Node 25 typings. Includes Node 23/24/25 API surface. Build target node20 in the service tsconfigs; verify `tsc` is clean and no Node-25-only typing leaks into runtime calls. |
| #256 | @types/node | 22.10.1 → 25.6.2 | services/auth | RISKY-MAJOR | Same as #248. Auth service is the production critical path; verify `pnpm -C services/auth typecheck && pnpm -C services/auth test` before merge. |
| #261 | @types/node | 22.10.1 → 25.6.2 | services/admin | RISKY-MAJOR | Same as #248. SvelteKit dev server uses Node API surface heavily; verify dev/build/test all clean. |
| #251 | typescript | 5.7.2 → 6.0.3 | services/auth | RISKY-MAJOR | TS 6 enables stricter `isolatedDeclarations`, sharper `--strict` defaults, removes a handful of deprecated lib types. High likelihood of new type errors in auth service which already runs `--strict`. Recommend bumping in a dedicated branch with `pnpm typecheck` first; consider waiting for TS 6.1 for ecosystem to stabilize. |
| #252 | @biomejs/biome | 1.9.4 → 2.4.15 | services/otel-lib-ts | RISKY-MAJOR | Biome 2 restructures the config schema (`biome.json` may need regeneration via `biome migrate`). Lint/format rules changed defaults; expect a wave of new diagnostics. Run `biome migrate` + commit the resulting diff in the same PR. |
| #259 | @biomejs/biome | 1.9.4 → 2.4.15 | services/admin | RISKY-MAJOR | Same as #252. The admin service has the largest TS surface area; expect 50+ lint findings. Coordinate with #252 so the biome version is consistent across the two TS services. |
| #258 | tailwind-variants | 0.3.0 → 3.2.2 | services/admin | RISKY-MAJOR | 10× pre-1.0 jump. API for `tv()`, `compoundVariants`, and slot composition all evolved. Every component using `tv()` must be visually regression-checked. Recommend a dedicated PR with Storybook (or Playwright snapshot) review before merge. |
| #260 | vite | 6.4.2 → 8.0.12 | services/admin | RISKY-MAJOR | Two major jumps. Vite 7 dropped Node 18 support; Vite 8 tightens default `optimizeDeps` behavior and the `define` plugin API. SvelteKit's `@sveltejs/kit` peerDep on vite may not yet allow 8. Verify SvelteKit version is compatible (vite 8 needs `@sveltejs/kit >= 2.x`) before merging. |
| #253 | vitest | 2.1.9 → 4.1.6 | services/auth | RISKY-MAJOR | Two major jumps. Vitest 3 changed `vi.mock` hoisting + config schema (`test.coverage` block rearranged); Vitest 4 removes the deprecated `globals` legacy flags and tightens default isolate behavior. Must land WITH #255 (coverage-v8) in the same merge or the coverage step will throw a peer-version error. |
| #254 | vitest | 2.1.9 → 4.1.6 | services/otel-lib-ts | RISKY-MAJOR | Same as #253. Must land WITH #250 (coverage-v8). |
| #255 | @vitest/coverage-v8 | 2.1.8 → 4.1.6 | services/auth | DEFER | Prereq: #253 (vitest 4). Coverage-v8 4.x peer-requires vitest 4.x; merging this alone will break the auth test command. After #253 lands and is verified, re-run Dependabot on this PR or merge together as a single squashed PR. |
| #250 | @vitest/coverage-v8 | 2.1.8 → 4.1.6 | services/otel-lib-ts | DEFER | Prereq: #254 (vitest 4). Same reasoning as #255. |

## Breaking-change deep dive (per RISKY-MAJOR PR)

### @types/node 22 → 25 (#248, #256, #261)

Node 23/24/25 added typings for: `node:sqlite` (built-in SQLite), expanded `node:test` surface, `URLPattern`, `globalThis.navigator`, refined `Buffer` type to narrow `Uint8Array<ArrayBufferLike>` in 24+. Risk areas:
- Any cast like `as Buffer` may need updating if downstream APIs typed against the new `Buffer<ArrayBufferLike>` form.
- `node:test` symbols overlap with vitest globals in some configs; verify no ambient-type collision.
- `process.env` types are unchanged; no risk there.

Runtime is unaffected (the package is types-only). Production Docker base images stay on Node 20-LTS regardless.

### typescript 5 → 6 (#251)

TS 6 highlights (sourced from upstream announcement notes):
- Stricter `useDefineForClassFields` (now default in `target: ESNext`).
- New `--erasableSyntaxOnly` flag aligns with the Node `--experimental-strip-types` runtime, useful future bait.
- Several deprecated `lib.d.ts` types removed (`String.prototype.substr`, `Object.prototype.__proto__` getter/setter).
- `noUncheckedIndexedAccess` interactions tightened against tuple types.

Auth service already runs strict mode; expect 5-15 new `error TS` diagnostics. None should be runtime-impacting, all are correctness wins.

### @biomejs/biome 1 → 2 (#252, #259)

Biome 2 is a config-breaking release:
- `biome.json` schema reorganized: `formatter` and `linter` are now nested under `assist` and `analyzer` namespaces.
- New rule categories: `nursery` rules graduated; some `style` rules moved to `correctness`.
- The `biome migrate` command auto-upgrades the config and emits the diff.

Run `pnpm -C <service> exec biome migrate --write` before merging each PR; commit the resulting `biome.json` change in the same PR. Without that step, the next `biome check` will exit non-zero.

### tailwind-variants 0.3 → 3.2 (#258)

Pre-1.0 to v3 is effectively a full library rewrite:
- `compoundVariants` matching semantics: now strict-equality on all keys (was previously partial-match in 0.x).
- `slots` API renamed (`slots: {...}` retained but `extend` semantics for slot composition changed; the `extend` parent slot keys now override children rather than the previous "child wins" rule).
- Tailwind CSS v4 is now a peer requirement (admin currently runs Tailwind v3; this PR will break the build unless Tailwind itself is also bumped, OR the v3.2 release contains a back-compat shim, which it does NOT in 3.x).

Recommend deferring this PR until the admin service has also migrated to Tailwind 4, OR pin tailwind-variants to the last 0.3.x release.

### vite 6 → 8 (#260)

Vite 7 (skipping over) + Vite 8 highlights:
- Vite 7: dropped Node 18; default browser target moved from `baseline-widely-available` to `baseline-2024`.
- Vite 8: tightened `optimizeDeps` defaults (`force: true` no longer implied by config changes); `define` plugin replacements now type-checked.
- Rollup bumped to v5; some plugin authors haven't caught up.

The biggest unknown is SvelteKit's peerDep. Check `services/admin/package.json` for the `@sveltejs/kit` version; SvelteKit 2.x supports vite 7+ but only as of `@sveltejs/kit >= 2.30`. If the admin service is on an older kit version, this PR will fail `pnpm install` with peer-dep errors.

### vitest 2 → 4 (#253, #254)

Two-major-version jump:
- Vitest 3: `vi.mock` factory hoisting fixed (some module mocks that "worked" in v2 due to hoisting bugs now fail correctly); config schema for `test.coverage` block reorganized.
- Vitest 4: removed deprecated `globals: true` legacy import shim; default `isolate: true` is now stricter (each test file gets a fresh module graph).
- Pool defaults changed: `forks` is now default on Linux; `threads` was previously default.

The coverage-v8 plugin's peer-version constraint REQUIRES vitest 4.x to match. Hence #255 and #250 cannot land alone.

## Recommended next actions for Phil

1. Tackle the vitest 2→4 pair as a single combined PR per service: rebase #255 onto #253, run `pnpm -C services/auth test --coverage`, fix fallout, squash-merge as one. Repeat for otel-lib-ts (#250 + #254).
2. Biome 1→2 (#252, #259): run `biome migrate` in each service, commit the config diff, then merge.
3. @types/node 22→25 (#248, #256, #261): low-risk in practice; can be merged after a single `pnpm typecheck` confirmation per service.
4. typescript 5→6 (#251): single dedicated PR, schedule for a low-traffic window.
5. tailwind-variants (#258): block on Tailwind v4 migration; do not merge alone.
6. vite 6→8 (#260): check SvelteKit peerDep first; may need to bump SvelteKit in the same PR.

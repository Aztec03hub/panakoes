import { sveltekit } from "@sveltejs/kit/vite";
import { svelteTesting } from "@testing-library/svelte/vite";
import { defineConfig } from "vitest/config";

// `svelteTesting()` registers a Vite plugin that bridges
// `@testing-library/svelte` with Svelte 5's compiler. Without it the test
// runner attempts to print pre-compiled TypeScript AST nodes through `esrap`,
// which fails on type annotations. See
// https://testing-library.com/docs/svelte-testing-library/setup#svelte-5
export default defineConfig({
  plugins: [sveltekit(), svelteTesting()],
  test: {
    globals: true,
    environment: "jsdom",
    include: ["src/**/*.{test,spec}.{js,ts}", "tests/**/*.{test,spec}.{js,ts}"],
    // Playwright specs live under `tests/e2e/**` and use `@playwright/test`,
    // which throws "test() did not expect to be called here" if vitest tries
    // to load them. The Playwright runner owns those files via
    // `pnpm test:e2e` (see playwright.config.ts -> testDir: "./tests/e2e").
    exclude: ["node_modules/**", "tests/e2e/**", ".svelte-kit/**", "build/**"],
    setupFiles: ["./tests/setup.ts"],
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "json-summary"],
      include: ["src/lib/**/*.ts", "src/lib/**/*.svelte.ts"],
      exclude: ["src/lib/types.ts", "src/lib/components/**", "**/*.d.ts"],
      thresholds: {
        lines: 70,
        functions: 70,
        branches: 70,
        statements: 70,
        // Auth path is per-CLAUDE.md a 100%-coverage path.
        "src/lib/auth.svelte.ts": {
          lines: 100,
          functions: 100,
          branches: 100,
          statements: 100,
        },
      },
    },
  },
});

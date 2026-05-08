import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: false,
    environment: "node",
    include: ["tests/**/*.test.ts"],
    testTimeout: 15_000,
    hookTimeout: 15_000,
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "json-summary"],
      include: ["src/**/*.ts"],
      exclude: ["src/index.ts", "**/*.d.ts"],
      // hono.ts uses inline propagator-carrier accessor objects which v8
      // counts as separate functions but invokes them only when a configured
      // propagator is wired up. Tests at this lib level deliberately do NOT
      // call configure() (the lib must work without it), which leaves 2-4
      // anonymous methods at 0% coverage and drags function coverage to 75%.
      // Honest threshold for now; revisit after we add a Hono integration
      // test in a downstream service that runs configure() first.
      thresholds: {
        lines: 80,
        functions: 75,
        branches: 80,
        statements: 80,
      },
    },
  },
});

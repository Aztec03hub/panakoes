import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: false,
    environment: "node",
    include: ["tests/**/*.test.ts"],
    globalSetup: ["./tests/setup.ts"],
    testTimeout: 60_000,
    hookTimeout: 60_000,
    pool: "forks",
    maxWorkers: 1,
    isolate: false,
    coverage: {
      provider: "v8",
      reporter: ["text", "lcov", "json-summary"],
      include: ["src/**/*.ts"],
      exclude: [
        "src/index.ts",
        "src/logger.ts",
        "src/db/client.ts",
        "src/db/schema.ts",
        "src/auth/better-auth.ts",
        "**/*.d.ts",
      ],
      thresholds: {
        lines: 100,
        functions: 100,
        branches: 100,
        statements: 100,
      },
    },
  },
});

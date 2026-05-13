/// <reference types="node" />
import { defineConfig, devices } from "@playwright/test";

// Playwright config for the admin SPA. Used both by svelte-worker for
// visual-verification screenshots after Svelte changes and for future e2e
// suites. Kept narrow on purpose: one chromium project, dev-server auto-start.
//
// Run locally:   pnpm exec playwright test
// Screenshot:    pnpm exec playwright test --update-snapshots
// Artifacts land in `.playwright-artifacts/` which is gitignored at repo root.

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "../../.playwright-artifacts/admin",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: process.env.PLAYWRIGHT_BASE_URL
    ? undefined
    : {
        command: "pnpm dev --host 127.0.0.1 --port 5173",
        url: "http://localhost:5173",
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});

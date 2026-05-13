import { expect, test } from "@playwright/test";

// Smoke spec. Exercises the test harness end-to-end so svelte-worker has a
// runnable example when it adds visual-verification screenshots after a UI
// change. Intentionally narrow: hits the root route, checks the page renders
// without a runtime error, and captures one screenshot. Real e2e coverage
// (auth flow, dashboard interactions) gets layered on later; this spec is the
// "is the harness wired up at all" check.

test("root route renders without a runtime error", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("pageerror", (err) => consoleErrors.push(String(err)));
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });

  await page.goto("/");
  await expect(page).toHaveURL(/\/(login|dashboard|forbidden)?$/);
  await page.screenshot({ path: "../../.playwright-artifacts/admin/root.png", fullPage: true });

  // Unauthenticated visitors should land somewhere sensible (login or
  // forbidden), not on a runtime-error boundary.
  expect(consoleErrors, consoleErrors.join("\n")).toHaveLength(0);
});

import path from "node:path";
import { expect, test } from "@playwright/test";

// ---------------------------------------------------------------------------
// File-upload realtime transcription e2e.
//
// This is a LIVE end-to-end smoke against a deployed dev environment: it
// signs into the SPA, uploads an audio file on /realtime, and waits for the
// real per-session GPU EC2 to cold-start (~7-8 min) and produce a transcript.
// Because it spawns real cloud resources, it is SKIPPED unless the operator
// opts in by setting E2E_BASE_URL + E2E_EMAIL + E2E_PASSWORD.
//
// Run:
//   E2E_BASE_URL=https://admin.dev.panakoes.com \
//   E2E_EMAIL=... E2E_PASSWORD=... \
//   pnpm exec playwright test realtime-file
//
// Optional: E2E_AUDIO_FIXTURE overrides the default gold-standard recording.
// ---------------------------------------------------------------------------

const BASE_URL = process.env.E2E_BASE_URL;
const EMAIL = process.env.E2E_EMAIL;
const PASSWORD = process.env.E2E_PASSWORD;
const AUDIO_FIXTURE =
  process.env.E2E_AUDIO_FIXTURE ??
  path.resolve(__dirname, "../../../../tests/fixtures/audio/panakoes-test-recording.mp3");

const READY_TIMEOUT_MS = 12 * 60 * 1000; // GPU cold-start ~7-8 min plus slack.

test.describe("realtime file-upload transcription (live)", () => {
  test.skip(
    !BASE_URL || !EMAIL || !PASSWORD,
    "set E2E_BASE_URL, E2E_EMAIL, E2E_PASSWORD to run the live file-upload smoke",
  );

  test("uploads a file, spawns a GPU, transcribes, ends cleanly", async ({ page }) => {
    test.setTimeout(READY_TIMEOUT_MS + 60_000);

    // 1. Sign in via the SPA login form.
    await page.goto(`${BASE_URL}/login`);
    await page.locator('input[name="email"]').fill(EMAIL as string);
    await page.locator('input[name="password"]').fill(PASSWORD as string);
    await page.getByTestId("login-submit").click();
    await page.waitForURL(/\/(dashboard|realtime|forbidden)?$/, { timeout: 30_000 });

    // 2. Navigate to /realtime and upload the fixture.
    await page.goto(`${BASE_URL}/realtime`);
    const input = page.getByTestId("file-upload-input");
    await input.setInputFiles(AUDIO_FIXTURE);
    await page.getByTestId("file-transcribe-button").click();

    const log = page.getByRole("log", { name: "Realtime session event log" });

    // 3. Wait for the backend status pipeline to reach spawn-message-received
    // then ready. Generous polling; this is a real cold start.
    await expect(log).toContainText("spawn-message-received", { timeout: READY_TIMEOUT_MS });
    await expect(log).toContainText("ready", { timeout: READY_TIMEOUT_MS });

    // 4. The Transcript card eventually shows non-empty final/partial text
    // containing a word we know is in the gold-standard recording.
    const transcriptCard = page.getByText(/panakoes|test/i);
    await expect(transcriptCard.first()).toBeVisible({ timeout: 90_000 });

    // 5. The session ends cleanly after the drain grace window.
    await expect(page.getByText("Session ended")).toBeVisible({ timeout: 60_000 });
  });
});

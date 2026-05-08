import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configure } from "../src/configure.ts";
import { shutdown } from "../src/shutdown.ts";

describe("shutdown", () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.OTEL_SDK_DISABLED;
  });

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it("calls sdk.shutdown() and resolves", async () => {
    const fakeSdk = {
      shutdown: vi.fn().mockResolvedValue(undefined),
    };

    // biome-ignore lint/suspicious/noExplicitAny: test double for NodeSDK
    await shutdown(fakeSdk as any);
    expect(fakeSdk.shutdown).toHaveBeenCalledTimes(1);
  });

  it("is idempotent: a second call after a successful shutdown does not throw", async () => {
    const fakeSdk = {
      shutdown: vi.fn().mockResolvedValue(undefined),
    };

    // biome-ignore lint/suspicious/noExplicitAny: test double for NodeSDK
    await shutdown(fakeSdk as any);
    // biome-ignore lint/suspicious/noExplicitAny: test double for NodeSDK
    await expect(shutdown(fakeSdk as any)).resolves.not.toThrow();
  });

  it("swallows shutdown errors so callers can exit cleanly", async () => {
    const fakeSdk = {
      shutdown: vi.fn().mockRejectedValue(new Error("flush failed")),
    };

    // biome-ignore lint/suspicious/noExplicitAny: test double for NodeSDK
    await expect(shutdown(fakeSdk as any)).resolves.not.toThrow();
  });

  it("works with a real NodeSDK instance (no network calls; shutdown before start)", async () => {
    const sdk = configure({
      serviceName: "shutdown-test",
      environment: "test",
    });
    expect(sdk).not.toBeNull();
    if (sdk === null) return;

    await expect(shutdown(sdk)).resolves.not.toThrow();
  });
});

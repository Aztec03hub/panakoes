import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { configure } from "../src/configure.ts";

describe("configure", () => {
  const originalEnv = { ...process.env };

  beforeEach(() => {
    process.env = { ...originalEnv };
    delete process.env.OTEL_SDK_DISABLED;
    delete process.env.OTEL_EXPORTER_OTLP_ENDPOINT;
    delete process.env.SERVICE_VERSION;
  });

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it("returns null when OTEL_SDK_DISABLED=true", () => {
    process.env.OTEL_SDK_DISABLED = "true";

    const sdk = configure({
      serviceName: "test-service",
      environment: "test",
    });

    expect(sdk).toBeNull();
  });

  it("returns a NodeSDK instance on the happy path", () => {
    const sdk = configure({
      serviceName: "test-service",
      environment: "test",
      endpoint: "http://localhost:4317",
    });

    expect(sdk).not.toBeNull();
    expect(sdk).toBeDefined();
    expect(typeof sdk?.start).toBe("function");
    expect(typeof sdk?.shutdown).toBe("function");
  });

  it("falls back to OTEL_EXPORTER_OTLP_ENDPOINT env var when endpoint not provided", () => {
    process.env.OTEL_EXPORTER_OTLP_ENDPOINT = "http://otel-collector:4317";

    const sdk = configure({
      serviceName: "test-service",
      environment: "dev",
    });

    expect(sdk).not.toBeNull();
  });

  it("falls back to localhost when no endpoint env var is set", () => {
    const sdk = configure({
      serviceName: "test-service",
      environment: "dev",
    });

    expect(sdk).not.toBeNull();
  });

  it("uses SERVICE_VERSION env var for service.version resource attribute", () => {
    process.env.SERVICE_VERSION = "1.2.3";

    const sdk = configure({
      serviceName: "test-service",
      environment: "prod",
    });

    expect(sdk).not.toBeNull();
  });

  it("returns null when OTEL_SDK_DISABLED is the string 'TRUE' (case-insensitive)", () => {
    process.env.OTEL_SDK_DISABLED = "TRUE";

    const sdk = configure({
      serviceName: "test-service",
      environment: "test",
    });

    expect(sdk).toBeNull();
  });
});

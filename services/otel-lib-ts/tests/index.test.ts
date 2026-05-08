import { describe, expect, it } from "vitest";

import * as otel from "../src/index.ts";

describe("public API surface", () => {
  it("exports configure", () => {
    expect(typeof otel.configure).toBe("function");
  });

  it("exports instrumentHono", () => {
    expect(typeof otel.instrumentHono).toBe("function");
  });

  it("exports shutdown", () => {
    expect(typeof otel.shutdown).toBe("function");
  });

  it("exports getTracer and getMeter", () => {
    expect(typeof otel.getTracer).toBe("function");
    expect(typeof otel.getMeter).toBe("function");
  });

  it("exports instrumentations factory", () => {
    expect(typeof otel.instrumentations).toBe("function");
  });
});

import { describe, expect, it } from "vitest";

import { getMeter, getTracer } from "../src/getters.ts";

describe("getTracer", () => {
  it("returns a tracer instance with the given name", () => {
    const tracer = getTracer("test-tracer");
    expect(tracer).toBeDefined();
    expect(typeof tracer.startSpan).toBe("function");
  });
});

describe("getMeter", () => {
  it("returns a meter instance with the given name", () => {
    const meter = getMeter("test-meter");
    expect(meter).toBeDefined();
    expect(typeof meter.createCounter).toBe("function");
  });
});

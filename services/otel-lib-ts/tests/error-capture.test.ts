/**
 * Tests for the four error-capture hooks: `window.onerror`,
 * `window.onunhandledrejection`, `process.on("uncaughtException")`, and
 * `process.on("unhandledRejection")`.
 *
 * The OTEL JS API installs a no-op tracer by default when no provider is
 * registered, so we register a small in-memory tracer provider and verify
 * that handlers call `recordException` on the active span (or start a new
 * one when none is active).
 */

import { trace } from "@opentelemetry/api";
import {
  BasicTracerProvider,
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from "@opentelemetry/sdk-trace-base";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  installErrorCapture,
  isErrorCaptureDisabled,
  isErrorCaptureInstalled,
  recordOnActiveSpan,
  uninstallErrorCapture,
} from "../src/error-capture.ts";

let exporter: InMemorySpanExporter;
let provider: BasicTracerProvider;

beforeEach(() => {
  exporter = new InMemorySpanExporter();
  provider = new BasicTracerProvider({
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  trace.setGlobalTracerProvider(provider);
  delete process.env.OTEL_DISABLE_ERROR_CAPTURE;
  uninstallErrorCapture();
});

afterEach(async () => {
  uninstallErrorCapture();
  await provider.shutdown();
  trace.disable();
  exporter.reset();
});

describe("installErrorCapture", () => {
  it("is idempotent", () => {
    installErrorCapture();
    installErrorCapture();
    expect(isErrorCaptureInstalled()).toBe(true);
  });

  it("respects the OTEL_DISABLE_ERROR_CAPTURE opt-out env var", () => {
    process.env.OTEL_DISABLE_ERROR_CAPTURE = "true";
    expect(isErrorCaptureDisabled()).toBe(true);
    installErrorCapture();
    expect(isErrorCaptureInstalled()).toBe(false);
  });

  it("respects the browser opt-out global", () => {
    const g = globalThis as { __OTEL_DISABLE_ERROR_CAPTURE__?: boolean };
    g.__OTEL_DISABLE_ERROR_CAPTURE__ = true;
    try {
      expect(isErrorCaptureDisabled()).toBe(true);
    } finally {
      g.__OTEL_DISABLE_ERROR_CAPTURE__ = undefined;
    }
  });

  it("uninstall is safe to call without an install", () => {
    expect(() => uninstallErrorCapture()).not.toThrow();
  });
});

describe("recordOnActiveSpan", () => {
  it("records the exception on a freshly-minted span when none is active", () => {
    recordOnActiveSpan(new Error("orphan boom"));
    const spans = exporter.getFinishedSpans();
    const named = spans.find((s) => s.name === "uncaught_exception");
    expect(named).toBeDefined();
    expect(named?.events.some((e) => e.name === "exception")).toBe(true);
  });

  it("records the exception on a span retrieved via getActiveSpan", () => {
    // We stub `trace.getActiveSpan` for this assertion because the default
    // OTEL JS context manager is a no-op (active-span isn't propagated
    // without `AsyncHooksContextManager`); mocking is the minimal, focused
    // way to exercise the active-span branch of `recordOnActiveSpan`.
    const tracer = trace.getTracer("test");
    const span = tracer.startSpan("op");
    const spy = vi.spyOn(trace, "getActiveSpan").mockReturnValue(span);
    try {
      recordOnActiveSpan(new Error("inside boom"));
    } finally {
      spy.mockRestore();
      span.end();
    }
    const spans = exporter.getFinishedSpans();
    const named = spans.find((s) => s.name === "op");
    expect(named?.events.some((e) => e.name === "exception")).toBe(true);
  });
});

describe("Node process hooks", () => {
  it("uncaughtException listener records the exception", () => {
    installErrorCapture();
    // Emit the event directly so we don't actually crash the test runner.
    // Vitest itself attaches an uncaughtException listener; we add ours
    // alongside it via `process.on`, so simulating with `emit` is safe.
    const err = new Error("uncaught test boom");
    process.emit("uncaughtException", err);
    const spans = exporter.getFinishedSpans();
    expect(spans.some((s) => s.name === "uncaught_exception")).toBe(true);
  });

  it("unhandledRejection listener records the rejection", () => {
    installErrorCapture();
    const err = new Error("rejection test boom");
    // The Node typings for `process.emit("unhandledRejection", ...)` are
    // intentionally loose; cast to `any` is the canonical workaround in
    // tests that synthesize the event.
    // biome-ignore lint/suspicious/noExplicitAny: synthesizing a Node event
    (process.emit as any)("unhandledRejection", err, Promise.resolve());
    const spans = exporter.getFinishedSpans();
    expect(spans.some((s) => s.name === "uncaught_exception")).toBe(true);
  });

  it("unhandledRejection wraps non-Error reasons", () => {
    installErrorCapture();
    // biome-ignore lint/suspicious/noExplicitAny: synthesizing a Node event
    (process.emit as any)("unhandledRejection", "plain-string reason", Promise.resolve());
    // biome-ignore lint/suspicious/noExplicitAny: synthesizing a Node event
    (process.emit as any)("unhandledRejection", { weird: "object" }, Promise.resolve());
    const spans = exporter.getFinishedSpans();
    expect(spans.filter((s) => s.name === "uncaught_exception").length).toBeGreaterThanOrEqual(2);
  });
});

describe("Browser hooks via simulated window", () => {
  it("dispatches an error event and records it", () => {
    // Stub a minimal `window` on globalThis for this test only. We rely on
    // EventTarget which is universally available in the Node runtime used by
    // vitest, so the addEventListener path exercises real code.
    const fakeWindow = new EventTarget() as EventTarget;
    const g = globalThis as { window?: EventTarget };
    g.window = fakeWindow;
    try {
      installErrorCapture();
      const event = new Event("error");
      Object.defineProperty(event, "error", { value: new Error("browser boom") });
      fakeWindow.dispatchEvent(event);
      const spans = exporter.getFinishedSpans();
      expect(spans.some((s) => s.name === "uncaught_exception")).toBe(true);
    } finally {
      uninstallErrorCapture();
      delete g.window;
    }
  });

  it("dispatches an unhandledrejection event and records it", () => {
    const fakeWindow = new EventTarget() as EventTarget;
    const g = globalThis as { window?: EventTarget };
    g.window = fakeWindow;
    try {
      installErrorCapture();
      const event = new Event("unhandledrejection");
      Object.defineProperty(event, "reason", { value: new Error("rejected") });
      fakeWindow.dispatchEvent(event);
      const spans = exporter.getFinishedSpans();
      expect(spans.some((s) => s.name === "uncaught_exception")).toBe(true);
    } finally {
      uninstallErrorCapture();
      delete g.window;
    }
  });

  it("handles non-Error rejection reasons by wrapping them", () => {
    const fakeWindow = new EventTarget() as EventTarget;
    const g = globalThis as { window?: EventTarget };
    g.window = fakeWindow;
    try {
      installErrorCapture();
      const evt = new Event("unhandledrejection");
      Object.defineProperty(evt, "reason", { value: "plain string" });
      fakeWindow.dispatchEvent(evt);
      const spans = exporter.getFinishedSpans();
      expect(spans.some((s) => s.name === "uncaught_exception")).toBe(true);
    } finally {
      uninstallErrorCapture();
      delete g.window;
    }
  });

  it("handles an error event without an `error` property", () => {
    const fakeWindow = new EventTarget() as EventTarget;
    const g = globalThis as { window?: EventTarget };
    g.window = fakeWindow;
    try {
      installErrorCapture();
      fakeWindow.dispatchEvent(new Event("error"));
      const spans = exporter.getFinishedSpans();
      expect(spans.some((s) => s.name === "uncaught_exception")).toBe(true);
    } finally {
      uninstallErrorCapture();
      delete g.window;
    }
  });
});

import { SpanStatusCode, trace } from "@opentelemetry/api";
import type { NodeSDK } from "@opentelemetry/sdk-node";

/**
 * Capture uncaught exceptions and unhandled promise rejections as OTEL span
 * events. Mirrors the Python lib's `install_exception_capture()`.
 *
 * Four hooks are wired (where the host runtime supports them):
 *
 * 1. `window.onerror` for browser synchronous failures.
 * 2. `window.onunhandledrejection` for browser async failures.
 * 3. `process.on("uncaughtException")` for Node synchronous failures.
 * 4. `process.on("unhandledRejection")` for Node async failures.
 *
 * Each handler records the error on the active span if one exists; otherwise
 * it starts a one-shot `"uncaught_exception"` span so the failure is never
 * dropped silently. On fatal Node-side cases we force-flush the SDK before
 * the process exits.
 *
 * Opt-out: set `OTEL_DISABLE_ERROR_CAPTURE=true` (also honored on the browser
 * side via `globalThis.__OTEL_DISABLE_ERROR_CAPTURE__ === true`). Default is
 * on; errors going untracked is strictly worse than test noise.
 */

const TRACER_NAME = "@panakoes/otel.error-capture";

interface InstalledState {
  removers: Array<() => void>;
}

let installed: InstalledState | null = null;

/** Return true when the host has asked the lib to skip error-capture. */
export function isErrorCaptureDisabled(): boolean {
  if (typeof process !== "undefined" && process.env?.OTEL_DISABLE_ERROR_CAPTURE) {
    return process.env.OTEL_DISABLE_ERROR_CAPTURE.toLowerCase() === "true";
  }
  // Browser opt-out: hosts that need to silence capture in tests can set
  // this global before the lib boots.
  const g = globalThis as { __OTEL_DISABLE_ERROR_CAPTURE__?: boolean };
  return g.__OTEL_DISABLE_ERROR_CAPTURE__ === true;
}

function toError(reason: unknown): Error {
  if (reason instanceof Error) return reason;
  if (typeof reason === "string") return new Error(reason);
  try {
    return new Error(JSON.stringify(reason));
  } catch {
    return new Error(String(reason));
  }
}

/** Record `err` on the active span or a one-shot span if none is active. */
export function recordOnActiveSpan(err: Error): void {
  const tracer = trace.getTracer(TRACER_NAME);
  const active = trace.getActiveSpan();
  if (active) {
    active.recordException(err);
    active.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
    return;
  }
  const span = tracer.startSpan("uncaught_exception");
  span.recordException(err);
  span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
  span.end();
}

async function forceFlush(sdk: NodeSDK | null): Promise<void> {
  if (!sdk) return;
  try {
    await sdk.shutdown();
  } catch {
    // Never let telemetry teardown mask the underlying crash.
  }
}

/**
 * Install the four hooks. Idempotent: a second call is a no-op until
 * `uninstallErrorCapture()` runs.
 *
 * The `sdk` arg is optional. When provided, fatal Node-side handlers will
 * force-flush it before exit. The browser side ignores `sdk` (it has no
 * `process.exit` semantics).
 */
export function installErrorCapture(sdk: NodeSDK | null = null): void {
  if (installed) return;
  if (isErrorCaptureDisabled()) return;

  const removers: Array<() => void> = [];

  // Browser-side. We test the existence of `window` rather than relying on
  // `typeof window !== "undefined"` alone so JSDOM-based tests can exercise
  // this branch. The lib does NOT include DOM types in `tsconfig.lib` so we
  // type the window as a minimal `EventTarget`-shaped structural type.
  type BrowserEvent = { error?: unknown; reason?: unknown };
  type BrowserWindow = EventTarget;

  if (typeof globalThis !== "undefined" && "window" in globalThis) {
    const w = (globalThis as unknown as { window: BrowserWindow }).window;

    const onError = (event: Event): void => {
      // Some hosts surface `ErrorEvent` (real browsers) while others surface
      // a plain `Event` (Node EventTarget shim). Duck-type the `error`
      // property instead of relying on the `ErrorEvent` global, which is
      // undefined in vanilla Node.
      const candidate = (event as BrowserEvent).error;
      const err = candidate instanceof Error ? candidate : new Error("uncaught error");
      recordOnActiveSpan(err);
    };
    const onRejection = (event: Event): void => {
      const candidate = (event as BrowserEvent).reason;
      recordOnActiveSpan(toError(candidate));
    };

    w.addEventListener("error", onError);
    w.addEventListener("unhandledrejection", onRejection);
    removers.push(() => w.removeEventListener("error", onError));
    removers.push(() => w.removeEventListener("unhandledrejection", onRejection));
  }

  // Node-side.
  if (typeof process !== "undefined" && typeof process.on === "function") {
    const onUncaught = (err: Error): void => {
      recordOnActiveSpan(err);
      // Best-effort flush; do NOT call process.exit() ourselves. The host
      // (or Node's default) is responsible for the final exit decision so
      // we don't accidentally short-circuit signal handlers the service set
      // up earlier in its boot.
      void forceFlush(sdk);
    };
    const onRejection = (reason: unknown): void => {
      recordOnActiveSpan(toError(reason));
      void forceFlush(sdk);
    };

    process.on("uncaughtException", onUncaught);
    process.on("unhandledRejection", onRejection);
    removers.push(() => process.off("uncaughtException", onUncaught));
    removers.push(() => process.off("unhandledRejection", onRejection));
  }

  installed = { removers };
}

/** Restore prior handlers. Tests call this in afterEach. */
export function uninstallErrorCapture(): void {
  if (!installed) return;
  for (const remove of installed.removers) {
    try {
      remove();
    } catch {
      // Defensive: don't let a cleanup failure mask a test assertion.
    }
  }
  installed = null;
}

/** Return whether the hooks are currently active. */
export function isErrorCaptureInstalled(): boolean {
  return installed !== null;
}

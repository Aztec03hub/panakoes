import { type Meter, type Tracer, metrics, trace } from "@opentelemetry/api";

/**
 * Convenience wrapper around `trace.getTracer`. Returns a no-op tracer when
 * the SDK has not been started (e.g. when OTEL_SDK_DISABLED=true), which is
 * the desired behavior; instrumentation code should never need to null-check.
 */
export function getTracer(name: string): Tracer {
  return trace.getTracer(name);
}

/**
 * Convenience wrapper around `metrics.getMeter`. Same no-op semantics as
 * `getTracer` when the SDK is not started.
 */
export function getMeter(name: string): Meter {
  return metrics.getMeter(name);
}

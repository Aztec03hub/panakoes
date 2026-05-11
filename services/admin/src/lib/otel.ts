/**
 * Client-side OpenTelemetry bootstrap for the Panakoes admin SPA.
 *
 * The SPA is a static SvelteKit bundle on S3 + CloudFront, so the OTEL
 * runtime that ships with `@panakoes/otel` (which is built on
 * `@opentelemetry/sdk-node` and depends on Node built-ins like
 * `async_hooks`) cannot run here. This module wires the equivalent
 * browser-side SDK (`@opentelemetry/sdk-trace-web`) against the SAME
 * contract: identical resource attributes (`service.name`,
 * `service.version`, `service.namespace`, `deployment.environment`),
 * identical exporter endpoint env-var convention (`VITE_*` flavor of
 * `OTEL_EXPORTER_OTLP_ENDPOINT`), identical W3C trace-context
 * propagation, and identical "no-op when unset" semantics.
 *
 * Interview-defensible reasoning:
 *  - We use a browser SDK rather than the Node SDK because the SPA
 *    runs in a browser; sdk-node would crash on import.
 *  - The exporter is OTLP/HTTP (not gRPC like the Node side) because
 *    browsers cannot speak gRPC natively, and the standard ADOT
 *    collector exposes both endpoints (4317 gRPC, 4318 HTTP).
 *  - The SDK is lazy-imported from the layout root so it never
 *    enters the critical render path. If the env var is unset the
 *    library returns a no-op tracer and DOES NOT load any of the
 *    OTLP exporter code.
 *  - JWT claims (sub, role) are pulled from the existing session
 *    store and attached as span attributes; the JWT itself is
 *    NEVER logged.
 */

import {
  type Attributes,
  type Span,
  SpanStatusCode,
  context,
  propagation,
  trace,
} from "@opentelemetry/api";

/** Logical tracer name reused across the SPA. */
export const TRACER_NAME = "panakoes-admin-spa";

/** Resource-attribute key constants pinned as literals (see the Node lib for the same rationale). */
const ATTR_SERVICE_NAME = "service.name";
const ATTR_SERVICE_VERSION = "service.version";
const ATTR_SERVICE_NAMESPACE = "service.namespace";
const ATTR_DEPLOYMENT_ENVIRONMENT = "deployment.environment";

/** Convenience attribute keys exported so call-sites stay consistent. */
export const ATTR_USER_ID = "panakoes.user.id";
export const ATTR_USER_ROLE = "panakoes.user.role";
export const ATTR_HTTP_METHOD = "http.method";
export const ATTR_HTTP_URL = "http.url";
export const ATTR_HTTP_STATUS_CODE = "http.status_code";

/** Vite inlines `import.meta.env` at build time. Guarded for the test env. */
function readEnv(name: string): string {
  try {
    const env = (import.meta as { env?: Record<string, string | undefined> }).env;
    return env?.[name] ?? "";
  } catch {
    return "";
  }
}

/** Module-level guard so {@link bootstrapOtel} is idempotent across HMR + remounts. */
let bootstrapped = false;

/** Test-only seam to reset the bootstrap latch between unit tests. */
export function _resetBootstrapForTests(): void {
  bootstrapped = false;
}

/** Options accepted by {@link bootstrapOtel}; defaults match the Panakoes contract. */
export interface BootstrapOptions {
  /** Override the exporter endpoint (defaults to `VITE_OTEL_EXPORTER_OTLP_ENDPOINT`). */
  endpoint?: string;
  /** Override the resolved environment tier (defaults to `VITE_DEPLOYMENT_ENVIRONMENT` or "dev"). */
  environment?: string;
  /** Override the service version (defaults to `VITE_SERVICE_VERSION` or `0.0.0`). */
  version?: string;
  /** Logger seam (defaults to `console.info` for the "disabled" boot log). */
  logger?: (msg: string) => void;
}

/**
 * Bootstrap the browser SDK once at app load. When the OTLP endpoint env
 * var is unset, the library emits a single info log and returns without
 * registering any SpanProcessor; the global tracer therefore stays no-op
 * and no network calls leave the page.
 *
 * Returns true if the SDK was wired, false if it stayed in no-op mode.
 */
export async function bootstrapOtel(opts: BootstrapOptions = {}): Promise<boolean> {
  if (bootstrapped) {
    return false;
  }
  bootstrapped = true;

  const endpoint = opts.endpoint ?? readEnv("VITE_OTEL_EXPORTER_OTLP_ENDPOINT");
  const environment = opts.environment ?? readEnv("VITE_DEPLOYMENT_ENVIRONMENT") ?? "dev";
  const version = opts.version ?? readEnv("VITE_SERVICE_VERSION") ?? "0.0.0";
  const logger = opts.logger ?? ((m: string) => console.info(m));

  if (endpoint === "") {
    logger("otel disabled, set VITE_OTEL_EXPORTER_OTLP_ENDPOINT to enable");
    return false;
  }

  // Lazy-import every SDK package so an unset endpoint never pulls the
  // exporter / sdk-trace-web bundle into the critical render path. With
  // env unset the bundle stays out of the entry chunk entirely and Vite
  // tree-shakes the chunk into a never-fetched dynamic import.
  const [
    { resourceFromAttributes },
    { WebTracerProvider, BatchSpanProcessor },
    { OTLPTraceExporter },
    { ZoneContextManager },
    { W3CTraceContextPropagator },
  ] = await Promise.all([
    import("@opentelemetry/resources"),
    import("@opentelemetry/sdk-trace-web"),
    import("@opentelemetry/exporter-trace-otlp-http"),
    import("@opentelemetry/context-zone"),
    import("@opentelemetry/core"),
  ]);

  // `@opentelemetry/resources@2.x` removed the `new Resource()` constructor;
  // the canonical browser-safe construction is now `resourceFromAttributes`
  // (mirrors the Node-side `@panakoes/otel` library's `configure.ts`).
  const resource = resourceFromAttributes({
    [ATTR_SERVICE_NAME]: "admin",
    [ATTR_SERVICE_NAMESPACE]: "panakoes",
    [ATTR_SERVICE_VERSION]: version || "0.0.0",
    [ATTR_DEPLOYMENT_ENVIRONMENT]: environment || "dev",
  });

  const exporter = new OTLPTraceExporter({ url: endpoint });
  const provider = new WebTracerProvider({
    resource,
    spanProcessors: [new BatchSpanProcessor(exporter)],
  });

  provider.register({
    contextManager: new ZoneContextManager(),
    propagator: new W3CTraceContextPropagator(),
  });

  return true;
}

/** Get the SPA's shared tracer. Returns a no-op tracer when the SDK is not registered. */
export function getTracer() {
  return trace.getTracer(TRACER_NAME);
}

/**
 * Attach a set of attributes to a span. Skips undefined / empty values
 * so we never emit attribute keys with blank string values (an
 * anti-pattern that pollutes the X-Ray index).
 */
export function addAttributes(span: Span, kv: Attributes): void {
  for (const [k, v] of Object.entries(kv)) {
    if (v === undefined || v === null) continue;
    if (typeof v === "string" && v === "") continue;
    span.setAttribute(k, v);
  }
}

/**
 * Inject the W3C `traceparent` (and `tracestate` when present) header
 * onto a fetch's outbound request init, derived from the active span's
 * context. No-op when there is no active span (e.g. SDK disabled).
 */
export function injectTraceContext(headers: Record<string, string>): Record<string, string> {
  const carrier: Record<string, string> = { ...headers };
  propagation.inject(context.active(), carrier);
  return carrier;
}

/**
 * Record an exception event on a span and set the span status to ERROR.
 * Mirrors the convention used by the Node lib's auto-instrumentations.
 */
export function recordException(span: Span, err: unknown): void {
  if (err instanceof Error) {
    span.recordException(err);
    span.setStatus({ code: SpanStatusCode.ERROR, message: err.message });
  } else {
    const msg = typeof err === "string" ? err : "unknown error";
    span.recordException({ name: "Error", message: msg });
    span.setStatus({ code: SpanStatusCode.ERROR, message: msg });
  }
}

/**
 * Map an HTTP status code to a span status. Mirrors the OTEL semantic
 * convention: 4xx/5xx are ERROR, everything else is UNSET (so the
 * default-OK convention from the backend can take precedence).
 */
export function setHttpStatus(span: Span, status: number): void {
  if (status >= 400) {
    span.setStatus({ code: SpanStatusCode.ERROR, message: `HTTP ${status}` });
  }
  span.setAttribute(ATTR_HTTP_STATUS_CODE, status);
}

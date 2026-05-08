import {
  ROOT_CONTEXT,
  SpanKind,
  SpanStatusCode,
  context,
  propagation,
  trace,
} from "@opentelemetry/api";
import type { Hono } from "hono";

const TRACER_NAME = "@panakoes/otel/hono";

/**
 * Wrap a Hono app with OpenTelemetry server-side instrumentation.
 *
 * Why manual: as of OTEL JS 0.55, there is no official Hono instrumentation
 * package. Hono runs on Node, Bun, Workers, and Deno; the auto HTTP
 * instrumentation only covers the Node `http` server, which misses requests
 * received via the WHATWG fetch entrypoint (workerd, edge runtimes, our
 * `app.request()` test harness). Doing this in-process via a Hono middleware
 * gives us coverage everywhere Hono runs.
 *
 * Behavior:
 * - Extracts incoming W3C trace context from request headers (continues
 *   distributed traces from upstream services or load balancers that propagate
 *   `traceparent`).
 * - Starts a SERVER-kind span named `<METHOD> <route>`, with route falling back
 *   to the raw path when Hono can't resolve a matched route (e.g. 404s).
 * - Records standard HTTP semantic-convention attributes on the span.
 * - Marks the span ERROR for 5xx responses or thrown handler errors, then
 *   re-throws so Hono's existing error handler runs unchanged.
 */
export function instrumentHono(app: Hono): void {
  const tracer = trace.getTracer(TRACER_NAME);

  app.use("*", async (c, next) => {
    const incomingContext = propagation.extract(ROOT_CONTEXT, c.req.raw.headers, {
      get(carrier, key) {
        return carrier.get(key) ?? undefined;
      },
      keys(carrier) {
        return Array.from(carrier.keys());
      },
    });

    const route = c.req.routePath ?? c.req.path;
    const method = c.req.method;
    const spanName = `${method} ${route}`;

    const span = tracer.startSpan(
      spanName,
      {
        kind: SpanKind.SERVER,
        attributes: {
          "http.request.method": method,
          "url.path": c.req.path,
          "http.route": route,
        },
      },
      incomingContext,
    );

    const activeContext = trace.setSpan(incomingContext, span);

    try {
      await context.with(activeContext, next);
      const status = c.res.status;
      span.setAttribute("http.response.status_code", status);
      if (status >= 500) {
        span.setStatus({ code: SpanStatusCode.ERROR });
      }
    } catch (err) {
      span.recordException(err as Error);
      span.setStatus({
        code: SpanStatusCode.ERROR,
        message: err instanceof Error ? err.message : String(err),
      });
      throw err;
    } finally {
      span.end();
    }
  });
}

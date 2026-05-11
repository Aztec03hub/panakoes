# @panakoes/otel

Shared OpenTelemetry instrumentation for Panakoes TypeScript services. Wraps `@opentelemetry/sdk-node`, the OTLP gRPC exporters, the standard Node auto-instrumentations, and a manual Hono server-side middleware behind a small, opinionated API.

## Why this exists

Panakoes runs a polyglot fleet of microservices (Python and TypeScript) and ships telemetry to AWS X-Ray + CloudWatch via the AWS Distro for OpenTelemetry (ADOT) collector over OTLP gRPC. Keeping the OTEL wiring in a shared lib means:

1. One place to bump OpenTelemetry versions across all TS services.
2. Resource attributes (`service.namespace=panakoes`, `service.version`, `deployment.environment`) are uniform across the fleet, which is what makes X-Ray service maps coherent.
3. Service code stays free of OTEL boilerplate. Each service writes a single `configure({...})` call at boot.

## Install

This package lives inside the Panakoes monorepo at `services/otel-lib-ts/`. Consume it via a workspace dependency in another service's `package.json`:

```json
{
  "dependencies": {
    "@panakoes/otel": "workspace:*"
  }
}
```

## Usage

```ts
// src/index.ts (service entrypoint)
import { configure, shutdown } from "@panakoes/otel";

const sdk = configure({
  serviceName: "auth",
  environment: process.env.NODE_ENV ?? "dev",
});
sdk?.start();

// ... boot the server ...

process.on("SIGTERM", async () => {
  if (sdk) await shutdown(sdk);
  process.exit(0);
});
```

For Hono apps, add the server-side middleware:

```ts
import { Hono } from "hono";
import { instrumentHono } from "@panakoes/otel";

const app = new Hono();
instrumentHono(app);
```

## API

| Export | Purpose |
|---|---|
| `configure(opts)` | Build a `NodeSDK` with OTLP gRPC trace + metric exporters and Node auto-instrumentations. Returns `null` when `OTEL_SDK_DISABLED=true`. |
| `shutdown(sdk)` | Idempotently flush and shut down. Swallows errors so process exit hooks don't hang. |
| `instrumentHono(app)` | Add server-side span middleware to a Hono app (W3C trace context propagation, SERVER-kind spans). |
| `getTracer(name)` | Convenience wrapper for `trace.getTracer`. |
| `getMeter(name)` | Convenience wrapper for `metrics.getMeter`. |
| `instrumentations` | Re-export of `getNodeAutoInstrumentations` for callers that want to compose their own SDK. |
| `installErrorCapture(sdk?)` | Auto-invoked by `configure()`. Wires `window.onerror`, `window.onunhandledrejection`, `process.on("uncaughtException")`, and `process.on("unhandledRejection")`. Each handler records the exception on the active span (or starts a one-shot `uncaught_exception` span when none is active) and force-flushes the SDK on fatal Node-side paths. |
| `uninstallErrorCapture()` | Restore the prior handlers. Called automatically by `shutdown()`. |
| `recordOnActiveSpan(err)` | Lower-level helper: record `err` on the active span or a freshly-minted one-shot span. |

## Error capture

`configure()` auto-installs four hooks so uncaught exceptions and rejections become OTEL exception events on the surrounding span:

- Browser: `window.onerror` and `window.onunhandledrejection` (when a `window` exists on `globalThis`).
- Node: `process.on("uncaughtException")` and `process.on("unhandledRejection")`.

Opt out by setting `OTEL_DISABLE_ERROR_CAPTURE=true` (Node) or `globalThis.__OTEL_DISABLE_ERROR_CAPTURE__ = true` (browser, before the lib boots). Defaults to on because silent errors are strictly worse than test noise. Tests that intentionally throw should flip the env var in their fixture.

## Environment variables

| Var | Default | Effect |
|---|---|---|
| `OTEL_SDK_DISABLED` | unset | When `true`, `configure()` returns `null` and no SDK is built. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP gRPC collector endpoint. |
| `OTEL_DISABLE_ERROR_CAPTURE` | unset | When `true`, skip auto-installing the error-capture hooks. |
| `SERVICE_VERSION` | `0.0.0` | Maps to `service.version` resource attribute (typically the git SHA or release tag). |

## Why a manual Hono middleware

There is no official Hono auto-instrumentation as of OTEL JS 0.55. The Node `http` auto-instrumentation only covers requests received by the Node `http` server, which misses requests via the WHATWG fetch entrypoint (workerd, edge runtimes, the `app.request()` test harness). Doing this in a Hono middleware gives uniform coverage across every runtime Hono targets.

## Development

```bash
pnpm install
pnpm test          # vitest, 21 tests
pnpm typecheck     # tsc --noEmit
pnpm lint          # biome check
pnpm build         # tsc -p tsconfig.build.json -> dist/
```

import { OTLPMetricExporter } from "@opentelemetry/exporter-metrics-otlp-grpc";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-grpc";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { PeriodicExportingMetricReader } from "@opentelemetry/sdk-metrics";
import { NodeSDK } from "@opentelemetry/sdk-node";
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from "@opentelemetry/semantic-conventions";

import { instrumentations } from "./instrumentations.ts";

/**
 * Options for {@link configure}.
 *
 * `serviceName` and `environment` are required so resource attributes are never
 * blank in the backend (CloudWatch / X-Ray group telemetry by these). `endpoint`
 * is optional and falls back to the standard OpenTelemetry env var, then to
 * localhost for dev parity with the ADOT collector default.
 */
export interface ConfigureOptions {
  /** Logical service name (e.g. "auth", "ingestion-api"). Maps to `service.name`. */
  serviceName: string;
  /** Deployment tier ("dev", "staging", "prod"). Maps to `deployment.environment`. */
  environment: string;
  /**
   * OTLP gRPC collector endpoint. Defaults to env `OTEL_EXPORTER_OTLP_ENDPOINT`,
   * then `http://localhost:4317` (ADOT collector default).
   */
  endpoint?: string;
}

const DEFAULT_OTLP_ENDPOINT = "http://localhost:4317";
// Standard semantic-conventions string for deployment environment. Pinned as a
// literal because the constant moved namespaces between OTEL JS minor versions
// and we want this lib to remain stable across upstream churn.
const ATTR_DEPLOYMENT_ENVIRONMENT = "deployment.environment";
const ATTR_SERVICE_NAMESPACE = "service.namespace";

/**
 * Configure and return a NodeSDK instance preloaded with the OTLP gRPC trace
 * and metric exporters and the standard Node auto-instrumentations (HTTP, fetch,
 * undici, etc.). Call `sdk.start()` once at process boot before importing any
 * instrumented modules whose internals you want traced.
 *
 * Returns null when `OTEL_SDK_DISABLED=true` so services can defang telemetry
 * during local dev or in environments without a collector without changing
 * code paths.
 */
export function configure(options: ConfigureOptions): NodeSDK | null {
  if (isSdkDisabled()) {
    return null;
  }

  const endpoint =
    options.endpoint ?? process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? DEFAULT_OTLP_ENDPOINT;
  const serviceVersion = process.env.SERVICE_VERSION ?? "0.0.0";

  // @opentelemetry/resources@2.x removed `new Resource()`; the canonical
  // construction is now the `resourceFromAttributes` factory.
  const resource = resourceFromAttributes({
    [ATTR_SERVICE_NAME]: options.serviceName,
    [ATTR_SERVICE_NAMESPACE]: "panakoes",
    [ATTR_SERVICE_VERSION]: serviceVersion,
    [ATTR_DEPLOYMENT_ENVIRONMENT]: options.environment,
  });

  const traceExporter = new OTLPTraceExporter({ url: endpoint });
  const metricReader = new PeriodicExportingMetricReader({
    exporter: new OTLPMetricExporter({ url: endpoint }),
  });

  return new NodeSDK({
    resource,
    traceExporter,
    metricReader,
    instrumentations: [instrumentations()],
  });
}

function isSdkDisabled(): boolean {
  const v = process.env.OTEL_SDK_DISABLED;
  if (v === undefined) return false;
  return v.toLowerCase() === "true";
}

/**
 * @panakoes/otel
 *
 * Shared OpenTelemetry instrumentation for Panakoes TypeScript services.
 *
 * Typical wiring at the top of a service entrypoint:
 *
 * ```ts
 * import { configure, shutdown } from "@panakoes/otel";
 *
 * const sdk = configure({ serviceName: "auth", environment: process.env.NODE_ENV ?? "dev" });
 * sdk?.start();
 *
 * process.on("SIGTERM", async () => {
 *   if (sdk) await shutdown(sdk);
 *   process.exit(0);
 * });
 * ```
 */

export { type ConfigureOptions, configure } from "./configure.ts";
export { getMeter, getTracer } from "./getters.ts";
export { instrumentHono } from "./hono.ts";
export { instrumentations } from "./instrumentations.ts";
export { shutdown } from "./shutdown.ts";

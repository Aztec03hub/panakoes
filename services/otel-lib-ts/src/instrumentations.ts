import { getNodeAutoInstrumentations } from "@opentelemetry/auto-instrumentations-node";

/**
 * Factory returning the standard set of Node auto-instrumentations
 * (HTTP, fetch, undici, gRPC, pg, redis, etc.). Exposed as a value-and-factory
 * so consumers can either pass it straight to the NodeSDK or invoke it to get
 * a fresh array (useful when overriding individual instrumentation configs).
 */
export const instrumentations = getNodeAutoInstrumentations;

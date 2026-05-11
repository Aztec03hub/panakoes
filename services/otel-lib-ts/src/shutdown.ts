import type { NodeSDK } from "@opentelemetry/sdk-node";

import { uninstallErrorCapture } from "./error-capture.ts";

/**
 * Registry of SDKs that have already been shut down. We track at the instance
 * level to make `shutdown()` idempotent: a second call against the same SDK
 * resolves immediately without invoking the underlying `shutdown` again. This
 * matters because process exit handlers can fire twice (signal + uncaught
 * exception path) and we don't want spurious "shutdown called twice" errors
 * to surface from the OTEL SDK on the way out.
 */
const shutdownRegistry = new WeakSet<NodeSDK>();

/**
 * Flush pending spans/metrics and shut down the SDK.
 *
 * Errors are caught and swallowed so callers can chain this in a `finally`
 * block during process shutdown without risking the process hanging on a
 * rejected promise. Idempotent: safe to call multiple times against the same
 * SDK.
 */
export async function shutdown(sdk: NodeSDK): Promise<void> {
  if (shutdownRegistry.has(sdk)) {
    return;
  }
  shutdownRegistry.add(sdk);

  try {
    await sdk.shutdown();
  } catch {
    // Intentionally swallowed; see docblock for reasoning.
  } finally {
    uninstallErrorCapture();
  }
}

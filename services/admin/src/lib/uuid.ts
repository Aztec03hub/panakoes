/**
 * UUIDv4 generation for Tier 3 idempotency keys.
 *
 * Every Tier 3 lifecycle submission mints a fresh idempotency key client-side
 * (see ADR-032 layer 2). The key is what gives the operator at-most-once
 * semantics: a flaky network or a stuck retry collapses to one effect because
 * admin-api dedups against the lifecycle-state DynamoDB table on this key.
 *
 * `crypto.randomUUID()` is available on every browser the admin dashboard
 * targets (Chrome 92+, Safari 15.4+, Firefox 95+) and on Node 19+ (vitest
 * runs on Node 22). We avoid pulling a polyfill because the freshness bar
 * is already that high; the dashboard is operator-only and we control the
 * supported browser matrix.
 *
 * The fallback path covers contrived test environments (or a hypothetical
 * future runtime without WebCrypto) using a Math.random shim that still
 * produces a v4-shaped string. The fallback is NOT cryptographically
 * meaningful; the key only needs to be collision-resistant for the
 * 24-hour idempotency-state TTL window, not unguessable.
 */
export function generateIdempotencyKey(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) {
    return c.randomUUID();
  }
  // Fallback: assemble a v4-shaped UUID by hand. xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
  // where y is one of 8, 9, a, b. Used only when WebCrypto is unavailable.
  const hex = "0123456789abcdef";
  const pick = (): string => hex[Math.floor(Math.random() * 16)] as string;
  let out = "";
  for (let i = 0; i < 36; i++) {
    if (i === 8 || i === 13 || i === 18 || i === 23) {
      out += "-";
    } else if (i === 14) {
      out += "4";
    } else if (i === 19) {
      out += hex[8 + Math.floor(Math.random() * 4)] as string;
    } else {
      out += pick();
    }
  }
  return out;
}

/** Validates a string against the canonical UUIDv4 format. Used in tests. */
export function isUuidV4(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value);
}

/**
 * `/.well-known/jwks.json` route.
 *
 * Two modes (selected by `AUTH_JWT_ALGORITHM`):
 *
 * - HS256 (default, phase 1 of ADR-041): HMAC has no public key. The
 *   endpoint returns `{"keys": []}` with a stable Cache-Control header so
 *   downstream services can wire their JWKS-fetch logic against a stable
 *   URL ahead of the phase-2 cutover. Returning 200 (not 503) keeps the
 *   contract stable across the HS256 -> RS256 transition.
 *
 * - RS256 (phase 1, opt-in via env; ADR-041): the route reads the public
 *   key from the injected `KmsSigner`, which caches the result for 10
 *   minutes by default. The returned JWKS contains exactly one key with
 *   `kid` matching the value the auth service stamps into JWT headers.
 */
import { Hono } from "hono";

import type { Config } from "../config.ts";
import type { JwksKey, KmsSigner } from "./kms-signer.ts";

// Re-exported for callers that historically imported the type from this
// module (kept the original public surface). New code should import from
// `./kms-signer.ts` directly.
export type { JwksKey } from "./kms-signer.ts";

export interface JwksRouteDeps {
  config: Pick<Config, "AUTH_JWT_ALGORITHM">;
  kmsSigner?: KmsSigner;
}

export function createJwksRoute(
  deps: JwksRouteDeps = { config: { AUTH_JWT_ALGORITHM: "HS256" } },
): Hono {
  const app = new Hono();
  const { config, kmsSigner } = deps;

  app.get("/.well-known/jwks.json", async (c) => {
    // 10-minute cache, matching the KMS public-key cache TTL. The public
    // key never rotates without an admin event, so a conservative cache
    // is safe and lowers KMS GetPublicKey traffic to ~6 requests/hour.
    c.header("Cache-Control", "public, max-age=600");

    if (config.AUTH_JWT_ALGORITHM === "RS256" && kmsSigner) {
      const jwks = await kmsSigner.getJwks();
      return c.json(jwks);
    }

    // HS256 fallback: empty key set with the documented 200 + cache
    // contract. See ADR-041 for the migration plan.
    return c.json({ keys: [] as JwksKey[] });
  });

  return app;
}

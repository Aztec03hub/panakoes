/**
 * `/.well-known/jwks.json` placeholder route.
 *
 * SCOPE NOTE: slice 1 ships HS256 with a shared secret (ADR-022); HS256
 * has no public key to expose, so this endpoint returns an empty key set.
 * The endpoint exists today so consumers can wire their JWKS-fetch logic
 * against a stable URL ahead of the slice-2 RS256 cutover. When RS256
 * lands, this handler surfaces the active RSA public key(s) keyed by
 * `kid`, with rotation semantics per ADR-022 (dual-publish, flip,
 * retire).
 *
 * The 200 status (rather than 503) is deliberate: standard JWKS clients
 * cache the `keys` array and treat empty as "no key found". Returning
 * 200 keeps that contract stable across the HS256 -> RS256 transition.
 */
import { Hono } from "hono";

export interface JwksKey {
  kty: string;
  use: string;
  kid: string;
  n: string;
  e: string;
  alg: string;
}

export function createJwksRoute(): Hono {
  const app = new Hono();
  app.get("/.well-known/jwks.json", (c) => {
    // 5-minute cache: matches the slice-2 rotation cadence. Slice-1
    // empty-keys responses are equally cacheable.
    c.header("Cache-Control", "public, max-age=300");
    return c.json({ keys: [] as JwksKey[] });
  });
  return app;
}

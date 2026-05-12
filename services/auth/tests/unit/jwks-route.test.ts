/**
 * Unit tests for the `/.well-known/jwks.json` route.
 *
 * Lives under `tests/unit/` because no database / testcontainer is
 * required: the route is constructed in isolation, the KMS signer is
 * stubbed, and assertions read the JSON response directly.
 */
import { describe, expect, it } from "vitest";

import { createJwksRoute } from "../../src/auth/jwks.ts";
import type { JwksDocument, KmsSigner } from "../../src/auth/kms-signer.ts";

function fakeSigner(jwks: JwksDocument, kid = "fake-kid"): KmsSigner {
  return {
    async sign() {
      return "x";
    },
    async kid() {
      return kid;
    },
    async getJwks() {
      return jwks;
    },
  };
}

async function fetchJwks(app: ReturnType<typeof createJwksRoute>): Promise<{
  status: number;
  body: unknown;
  cacheControl: string | null;
}> {
  const res = await app.fetch(new Request("http://test.local/.well-known/jwks.json"));
  const text = await res.text();
  return {
    status: res.status,
    body: text.length === 0 ? null : JSON.parse(text),
    cacheControl: res.headers.get("cache-control"),
  };
}

describe("createJwksRoute (HS256 default)", () => {
  it("returns an empty keys array when AUTH_JWT_ALGORITHM is HS256", async () => {
    const app = createJwksRoute({ config: { AUTH_JWT_ALGORITHM: "HS256" } });
    const res = await fetchJwks(app);
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ keys: [] });
  });

  it("sets a Cache-Control header so verifiers can cache the response", async () => {
    const app = createJwksRoute({ config: { AUTH_JWT_ALGORITHM: "HS256" } });
    const res = await fetchJwks(app);
    expect(res.cacheControl).toBeTruthy();
    expect(res.cacheControl).toContain("max-age=");
  });

  it("falls back to HS256 behaviour when the algorithm is RS256 but no signer is wired", async () => {
    // Defensive: the route is constructed with `{config, kmsSigner: undefined}`
    // in tests that focus on the legacy path. We surface an empty key set
    // rather than crash, so an environment misconfiguration does not break
    // the JWKS contract for downstream consumers.
    const app = createJwksRoute({ config: { AUTH_JWT_ALGORITHM: "RS256" } });
    const res = await fetchJwks(app);
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ keys: [] });
  });

  it("uses default deps when called with no arguments", async () => {
    // Default-argument coverage: protects the `createJwksRoute()` form.
    const app = createJwksRoute();
    const res = await fetchJwks(app);
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ keys: [] });
  });
});

describe("createJwksRoute (RS256)", () => {
  it("returns the JWKS document from the KMS signer", async () => {
    const jwks: JwksDocument = {
      keys: [
        {
          kty: "RSA",
          use: "sig",
          alg: "RS256",
          kid: "rs256-kid",
          n: "0vx7agoebGcQSuuPiLJXZpt...",
          e: "AQAB",
        },
      ],
    };
    const app = createJwksRoute({
      config: { AUTH_JWT_ALGORITHM: "RS256" },
      kmsSigner: fakeSigner(jwks),
    });
    const res = await fetchJwks(app);
    expect(res.status).toBe(200);
    expect(res.body).toEqual(jwks);
  });

  it("emits the same Cache-Control header in both modes (stable client contract)", async () => {
    const jwks: JwksDocument = {
      keys: [
        {
          kty: "RSA",
          use: "sig",
          alg: "RS256",
          kid: "x",
          n: "x",
          e: "AQAB",
        },
      ],
    };
    const app = createJwksRoute({
      config: { AUTH_JWT_ALGORITHM: "RS256" },
      kmsSigner: fakeSigner(jwks),
    });
    const res = await fetchJwks(app);
    expect(res.cacheControl).toContain("max-age=");
  });
});

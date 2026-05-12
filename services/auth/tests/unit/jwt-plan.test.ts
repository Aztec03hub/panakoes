import { decodeJwt } from "jose";
import { describe, expect, it } from "vitest";

import { signJwt, verifyJwt } from "../../src/auth/jwt.ts";
import { testConfig } from "../helpers.ts";

const config = testConfig();

describe("plan claim", () => {
  it("defaults to 'free' when sign-in does not provide a plan", async () => {
    const { token } = await signJwt(
      { sub: "u", email: "e@e.com", role: "user", jti: "j" },
      config,
    );
    const decoded = decodeJwt(token);
    expect(decoded.plan).toBe("free");

    const verified = await verifyJwt(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.plan).toBe("free");
    }
  });

  it("embeds the supplied 'pro' plan into the token", async () => {
    const { token } = await signJwt(
      { sub: "u", email: "e@e.com", role: "user", jti: "j", plan: "pro" },
      config,
    );
    const verified = await verifyJwt(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.plan).toBe("pro");
    }
  });

  it("embeds the supplied 'team' plan into the token", async () => {
    const { token } = await signJwt(
      { sub: "u", email: "e@e.com", role: "user", jti: "j", plan: "team" },
      config,
    );
    const verified = await verifyJwt(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.plan).toBe("team");
    }
  });

  it("falls back to 'free' when the JWT carries an unknown plan value", async () => {
    // Sign a token directly with a bogus plan claim to exercise the
    // verify-side fallback (a tampered or pre-rollout token should never
    // accidentally upgrade the caller).
    const { SignJWT } = await import("jose");
    const secret = new TextEncoder().encode(config.AUTH_JWT_SECRET);
    const issuedAt = Math.floor(Date.now() / 1000);
    const token = await new SignJWT({
      email: "e@e.com",
      role: "user",
      plan: "platinum",
    })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(issuedAt)
      .setExpirationTime(issuedAt + 3600)
      .sign(secret);

    const verified = await verifyJwt(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.plan).toBe("free");
    }
  });
});

import { SignJWT } from "jose";
import { describe, expect, it } from "vitest";

import {
  STEP_UP_EXPIRES_IN_SECONDS,
  signStepUpToken,
  verifyStepUpToken,
} from "../../src/auth/jwt.ts";
import { testConfig } from "../helpers.ts";

const config = testConfig();
const secretBytes = new TextEncoder().encode(config.AUTH_JWT_SECRET);

describe("signStepUpToken", () => {
  it("mints a token with step_up=true and a 5-minute expiry", async () => {
    const before = Date.now();
    const { token, expiresAt } = await signStepUpToken(
      { sub: "u1", email: "u1@example.com", role: "admin" },
      config,
    );
    expect(typeof token).toBe("string");
    expect(token.split(".")).toHaveLength(3);
    const expectedExp = before + STEP_UP_EXPIRES_IN_SECONDS * 1000;
    expect(expiresAt.getTime()).toBeGreaterThanOrEqual(expectedExp - 1000);
    expect(expiresAt.getTime()).toBeLessThanOrEqual(expectedExp + 2000);

    const verified = await verifyStepUpToken(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.sub).toBe("u1");
      expect(verified.claims.email).toBe("u1@example.com");
      expect(verified.claims.role).toBe("admin");
      expect(verified.claims.step_up).toBe(true);
    }
  });
});

describe("verifyStepUpToken", () => {
  it("rejects a token without the step_up claim as not a step-up token", async () => {
    // Build a regular-looking JWT WITHOUT step_up=true.
    const token = await new SignJWT({ email: "e@e.com", role: "user" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 60)
      .sign(secretBytes);
    const result = await verifyStepUpToken(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
    }
  });

  it("rejects expired step-up tokens", async () => {
    const past = Math.floor(Date.now() / 1000) - 10;
    const token = await new SignJWT({ email: "e@e.com", role: "admin", step_up: true })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(past - 60)
      .setExpirationTime(past)
      .sign(secretBytes);
    const result = await verifyStepUpToken(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("expired");
    }
  });

  it("rejects step-up tokens signed with a different secret", async () => {
    const wrongSecret = new TextEncoder().encode(
      "another-32-byte-or-longer-secret-not-the-real-one-for-tests",
    );
    const token = await new SignJWT({ email: "e@e.com", role: "admin", step_up: true })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 60)
      .sign(wrongSecret);
    const result = await verifyStepUpToken(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("invalid_signature");
    }
  });

  it("rejects step-up tokens with the wrong issuer", async () => {
    const token = await new SignJWT({ email: "e@e.com", role: "admin", step_up: true })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setIssuer("https://attacker.example.com")
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 60)
      .sign(secretBytes);
    const result = await verifyStepUpToken(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
    }
  });

  it("rejects malformed step-up tokens", async () => {
    const result = await verifyStepUpToken("not.a.real.jwt", config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("malformed");
    }
  });

  it("rejects step-up tokens with an unknown role", async () => {
    const token = await new SignJWT({ email: "e@e.com", role: "weird", step_up: true })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 60)
      .sign(secretBytes);
    const result = await verifyStepUpToken(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
    }
  });
});

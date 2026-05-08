import { SignJWT } from "jose";
import { describe, expect, it } from "vitest";

import { extractBearerToken, signJwt, verifyJwt } from "../../src/auth/jwt.ts";
import { testConfig } from "../helpers.ts";

const config = testConfig();
const secretBytes = new TextEncoder().encode(config.AUTH_JWT_SECRET);

describe("verifyJwt", () => {
  it("returns ok with claims for a valid token", async () => {
    const { token } = await signJwt(
      { sub: "user-1", email: "user1@example.com", role: "user", jti: "session-1" },
      config,
    );
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(true);
  });

  it("rejects expired tokens", async () => {
    const past = Math.floor(Date.now() / 1000) - 10;
    const token = await new SignJWT({ email: "e@e.com", role: "user" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(past - 60)
      .setExpirationTime(past)
      .sign(secretBytes);
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("expired");
    }
  });

  it("rejects tokens signed with the wrong secret", async () => {
    const wrongSecret = new TextEncoder().encode(
      "different-32-byte-or-longer-secret-not-matching-anything-real",
    );
    const token = await new SignJWT({ email: "e@e.com", role: "user" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(wrongSecret);
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("invalid_signature");
    }
  });

  it("rejects tokens with a wrong issuer", async () => {
    const token = await new SignJWT({ email: "e@e.com", role: "user" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer("https://attacker.example.com")
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(secretBytes);
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
    }
  });

  it("rejects tokens with a wrong audience", async () => {
    const token = await new SignJWT({ email: "e@e.com", role: "user" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience("some-other-audience")
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(secretBytes);
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
    }
  });

  it("rejects malformed tokens", async () => {
    const result = await verifyJwt("not.a.real.jwt.token", config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("malformed");
    }
  });

  it("rejects tokens with missing required claims", async () => {
    // Build a token missing the `email` payload claim, but otherwise valid.
    const token = await new SignJWT({ role: "user" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(secretBytes);
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
    }
  });

  it("rejects tokens missing the role claim", async () => {
    const token = await new SignJWT({ email: "e@e.com" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(secretBytes);
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
      if (result.error.kind === "claim_mismatch") {
        expect(result.error.reason).toBe("invalid role");
      }
    }
  });

  it("rejects tokens carrying an unknown role", async () => {
    const token = await new SignJWT({ email: "e@e.com", role: "superuser" })
      .setProtectedHeader({ alg: "HS256", typ: "JWT" })
      .setSubject("u")
      .setJti("j")
      .setIssuer(config.AUTH_JWT_ISSUER)
      .setAudience(config.AUTH_JWT_AUDIENCE)
      .setIssuedAt(Math.floor(Date.now() / 1000))
      .setExpirationTime(Math.floor(Date.now() / 1000) + 600)
      .sign(secretBytes);
    const result = await verifyJwt(token, config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("claim_mismatch");
    }
  });
});

describe("extractBearerToken", () => {
  it("returns null when the header is missing", () => {
    expect(extractBearerToken(null)).toBeNull();
    expect(extractBearerToken(undefined)).toBeNull();
    expect(extractBearerToken("")).toBeNull();
  });

  it("extracts the token from a well-formed Bearer header", () => {
    expect(extractBearerToken("Bearer abc.def.ghi")).toBe("abc.def.ghi");
  });

  it("is case-insensitive on the scheme", () => {
    expect(extractBearerToken("bearer abc")).toBe("abc");
    expect(extractBearerToken("BEARER abc")).toBe("abc");
  });

  it("trims surrounding whitespace", () => {
    expect(extractBearerToken("  Bearer abc  ")).toBe("abc");
  });

  it("returns null for a non-Bearer scheme", () => {
    expect(extractBearerToken("Basic abc")).toBeNull();
  });
});

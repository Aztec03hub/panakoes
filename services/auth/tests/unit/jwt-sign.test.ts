import { describe, expect, it } from "vitest";

import { signJwt, verifyJwt } from "../../src/auth/jwt.ts";
import { TEST_JWT_SECRET, testConfig } from "../helpers.ts";

const config = testConfig();

describe("signJwt", () => {
  it("mints a token with the spec'd claims", async () => {
    const { token, expiresAt } = await signJwt(
      { sub: "user-uuid", email: "user@example.com", role: "user", jti: "session-uuid" },
      config,
    );
    expect(typeof token).toBe("string");
    expect(token.split(".")).toHaveLength(3);
    expect(expiresAt.getTime()).toBeGreaterThan(Date.now());

    const verified = await verifyJwt(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.sub).toBe("user-uuid");
      expect(verified.claims.email).toBe("user@example.com");
      expect(verified.claims.role).toBe("user");
      expect(verified.claims.jti).toBe("session-uuid");
      expect(verified.claims.exp).toBeGreaterThan(verified.claims.iat);
    }
  });

  it("uses HS256 in the JWT header", async () => {
    const { token } = await signJwt({ sub: "u", email: "e@e.com", role: "user", jti: "j" }, config);
    const headerSegment = token.split(".")[0] ?? "";
    const headerJson = Buffer.from(headerSegment, "base64url").toString("utf8");
    const header = JSON.parse(headerJson);
    expect(header.alg).toBe("HS256");
    expect(header.typ).toBe("JWT");
  });

  it("respects the configured expiry window", async () => {
    const shortConfig = testConfig({ AUTH_JWT_EXPIRES_IN_SECONDS: 60 });
    const { expiresAt } = await signJwt(
      { sub: "u", email: "e@e.com", role: "user", jti: "j" },
      shortConfig,
    );
    const delta = expiresAt.getTime() - Date.now();
    expect(delta).toBeGreaterThan(50_000);
    expect(delta).toBeLessThan(70_000);
  });

  it("rejects tampered payloads on verify (signature mismatch)", async () => {
    const { token } = await signJwt({ sub: "u", email: "e@e.com", role: "user", jti: "j" }, config);
    const [header, _payload, signature] = token.split(".");
    const tamperedPayload = Buffer.from(
      JSON.stringify({ sub: "attacker", email: "x@x.com", role: "admin", jti: "stolen" }),
    ).toString("base64url");
    const tampered = `${header}.${tamperedPayload}.${signature}`;
    const result = await verifyJwt(tampered, config);
    expect(result.ok).toBe(false);
  });

  it("embeds the admin role when minted for an admin user", async () => {
    const { token } = await signJwt(
      { sub: "u", email: "admin@example.com", role: "admin", jti: "j" },
      config,
    );
    const verified = await verifyJwt(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.role).toBe("admin");
    }
  });

  it("uses TEST_JWT_SECRET sentinel correctly", () => {
    expect(TEST_JWT_SECRET.length).toBeGreaterThanOrEqual(32);
  });

  it("defaults the plan claim to 'free' when omitted", async () => {
    const { token } = await signJwt(
      { sub: "u", email: "e@e.com", role: "user", jti: "j" },
      config,
    );
    const verified = await verifyJwt(token, config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.plan).toBe("free");
    }
  });

  it("embeds the supplied plan when the caller passes plan: 'pro'", async () => {
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

  it("embeds 'team' when the caller passes plan: 'team'", async () => {
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
});

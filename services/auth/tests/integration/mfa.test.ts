import { type TOTP, URI } from "otpauth";
import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { signStepUpToken } from "../../src/auth/jwt.ts";
import { buildTestApp, jsonRequest, truncateAll } from "../helpers.ts";

const app = buildTestApp();

beforeEach(async () => {
  await truncateAll(app.db);
});

afterAll(async () => {
  await app.cleanup();
});

async function signUpUser(
  email: string,
  password: string,
): Promise<{ token: string; userId: string }> {
  const res = await jsonRequest(app, "/auth/sign-up", { body: { email, password } });
  if (res.status !== 201) {
    throw new Error(`signup failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
  const body = res.body as { token: string; user: { id: string } };
  return { token: body.token, userId: body.user.id };
}

async function adminSignIn(email: string, password: string): Promise<string> {
  await signUpUser(email, password);
  await app.db.execute(`UPDATE "user" SET "role" = 'admin' WHERE "email" = '${email}'`);
  const res = await jsonRequest(app, "/auth/sign-in", { body: { email, password } });
  if (res.status !== 200) {
    throw new Error(`signin failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
  return (res.body as { token: string }).token;
}

describe("POST /auth/mfa/enroll", () => {
  it("returns 401 when no bearer token is supplied", async () => {
    const res = await jsonRequest(app, "/auth/mfa/enroll");
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("missing_bearer_token");
  });

  it("returns 401 when the bearer token is malformed", async () => {
    const res = await jsonRequest(app, "/auth/mfa/enroll", {
      headers: { authorization: "Bearer not.a.real.token" },
    });
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("invalid_token");
  });

  it("returns 403 when the user is not an admin", async () => {
    const { token } = await signUpUser("user1@example.com", "correct horse battery staple");
    const res = await jsonRequest(app, "/auth/mfa/enroll", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(403);
    expect((res.body as { error: string }).error).toBe("forbidden");
  });

  it("returns secret_key + qr_code_uri for admins", async () => {
    const adminToken = await adminSignIn("admin1@example.com", "correct horse battery staple");
    const res = await jsonRequest(app, "/auth/mfa/enroll", {
      headers: { authorization: `Bearer ${adminToken}` },
    });
    expect(res.status).toBe(200);
    const body = res.body as { secret_key: string; qr_code_uri: string };
    expect(body.secret_key).toMatch(/^[A-Z2-7]+$/);
    expect(body.qr_code_uri.startsWith("otpauth://totp/")).toBe(true);
    expect(body.qr_code_uri).toContain(encodeURIComponent("admin1@example.com"));
  });
});

describe("POST /auth/mfa/verify", () => {
  it("returns 401 when no bearer token is supplied", async () => {
    const res = await jsonRequest(app, "/auth/mfa/verify", {
      body: { code: "123456", secret_key: "JBSWY3DPEHPK3PXP" },
    });
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("missing_bearer_token");
  });

  it("returns 401 when the bearer token is invalid", async () => {
    const res = await jsonRequest(app, "/auth/mfa/verify", {
      headers: { authorization: "Bearer bogus" },
      body: { code: "123456", secret_key: "JBSWY3DPEHPK3PXP" },
    });
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("invalid_token");
  });

  it("returns 400 with invalid_request for malformed bodies (non-6-digit code)", async () => {
    const { token } = await signUpUser("verify1@example.com", "correct horse battery staple");
    const tooShort = await jsonRequest(app, "/auth/mfa/verify", {
      headers: { authorization: `Bearer ${token}` },
      body: { code: "12345", secret_key: "JBSWY3DPEHPK3PXP" },
    });
    expect(tooShort.status).toBe(400);
    expect((tooShort.body as { error: string }).error).toBe("invalid_request");

    const nonNumeric = await jsonRequest(app, "/auth/mfa/verify", {
      headers: { authorization: `Bearer ${token}` },
      body: { code: "abcdef", secret_key: "JBSWY3DPEHPK3PXP" },
    });
    expect(nonNumeric.status).toBe(400);

    const noBody = await jsonRequest(app, "/auth/mfa/verify", {
      headers: { authorization: `Bearer ${token}` },
      body: undefined,
    });
    expect(noBody.status).toBe(400);
  });

  it("returns 401 with invalid_mfa_code when the code is wrong", async () => {
    const { token } = await signUpUser("verify2@example.com", "correct horse battery staple");
    const res = await jsonRequest(app, "/auth/mfa/verify", {
      headers: { authorization: `Bearer ${token}` },
      // Valid base32 (16 chars, RFC 4648 alphabet) but not a real enrolment.
      body: { code: "000000", secret_key: "JBSWY3DPEHPK3PXP" },
    });
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("invalid_mfa_code");
  });

  it("issues a step-up token when the code matches the supplied secret", async () => {
    const { token } = await signUpUser("verify3@example.com", "correct horse battery staple");

    // Enrol via the route; the user role suffices since we only need the secret
    // to feed back to /verify (admin gating only protects /enroll).
    // Use enrollMfa directly via the same library to avoid needing admin here.
    const enroll = await jsonRequest(app, "/auth/mfa/enroll", {
      headers: { authorization: `Bearer ${await adminSignInExisting("verify3@example.com")}` },
    });
    const offer = enroll.body as { secret_key: string; qr_code_uri: string };
    const totp = URI.parse(offer.qr_code_uri) as TOTP;
    const code = totp.generate();

    const res = await jsonRequest(app, "/auth/mfa/verify", {
      headers: { authorization: `Bearer ${token}` },
      body: { code, secret_key: offer.secret_key },
    });
    expect(res.status).toBe(200);
    const body = res.body as { step_up_token: string; expiresAt: string };
    expect(typeof body.step_up_token).toBe("string");
    expect(body.step_up_token.split(".")).toHaveLength(3);
  });
});

describe("POST /auth/mfa/challenge", () => {
  it("returns 401 with WWW-Authenticate: StepUp when no token is supplied", async () => {
    const res = await jsonRequest(app, "/auth/mfa/challenge");
    expect(res.status).toBe(401);
    expect(res.headers.get("www-authenticate")).toBe("StepUp");
    expect((res.body as { error: string }).error).toBe("step_up_required");
  });

  it("returns 401 + StepUp when the supplied token is a regular access token", async () => {
    const { token } = await signUpUser("challenge1@example.com", "correct horse battery staple");
    const res = await jsonRequest(app, "/auth/mfa/challenge", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(401);
    expect(res.headers.get("www-authenticate")).toBe("StepUp");
  });

  it("returns 200 when the supplied token is a valid step-up token", async () => {
    const stepUp = await signStepUpToken(
      { sub: "00000000-0000-0000-0000-000000000111", email: "ad@example.com", role: "admin" },
      app.config,
    );
    const res = await jsonRequest(app, "/auth/mfa/challenge", {
      headers: { authorization: `Bearer ${stepUp.token}` },
    });
    expect(res.status).toBe(200);
    const body = res.body as { step_up: boolean; user: { role: string } };
    expect(body.step_up).toBe(true);
    expect(body.user.role).toBe("admin");
  });
});

/** Promote an already-signed-up user to admin and return a fresh token. */
async function adminSignInExisting(email: string): Promise<string> {
  await app.db.execute(`UPDATE "user" SET "role" = 'admin' WHERE "email" = '${email}'`);
  const res = await jsonRequest(app, "/auth/sign-in", {
    body: { email, password: "correct horse battery staple" },
  });
  if (res.status !== 200) {
    throw new Error(`signin failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
  return (res.body as { token: string }).token;
}

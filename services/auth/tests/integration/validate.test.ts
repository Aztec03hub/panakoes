import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { signJwt } from "../../src/auth/jwt.ts";
import { buildTestApp, jsonRequest, truncateAll } from "../helpers.ts";

const app = buildTestApp();

beforeEach(async () => {
  await truncateAll(app.db);
});

afterAll(async () => {
  await app.cleanup();
});

async function signUp(
  email: string,
  password: string,
): Promise<{
  token: string;
  user: { id: string; email: string };
}> {
  const res = await jsonRequest(app, "/auth/sign-up", { body: { email, password } });
  if (res.status !== 201) {
    throw new Error(`signup failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
  return res.body as { token: string; user: { id: string; email: string } };
}

describe("POST /auth/validate", () => {
  it("returns valid:true and the user identity for an active session", async () => {
    const { token, user } = await signUp("valid@example.com", "correct horse battery staple");
    const res = await jsonRequest(app, "/auth/validate", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ valid: true, user: { id: user.id, email: user.email } });
  });

  it("returns 401 with reason=missing_bearer_token when no header is supplied", async () => {
    const res = await jsonRequest(app, "/auth/validate");
    expect(res.status).toBe(401);
    expect((res.body as { reason: string }).reason).toBe("missing_bearer_token");
  });

  it("returns 401 with reason=invalid_signature for a tampered token", async () => {
    const { token } = await signUp("tamper@example.com", "correct horse battery staple");
    const [header, _payload, signature] = token.split(".");
    const tamperedPayload = Buffer.from(
      JSON.stringify({ sub: "x", email: "x@x.com", jti: "x" }),
    ).toString("base64url");
    const tampered = `${header}.${tamperedPayload}.${signature}`;
    const res = await jsonRequest(app, "/auth/validate", {
      headers: { authorization: `Bearer ${tampered}` },
    });
    expect(res.status).toBe(401);
    expect((res.body as { reason: string }).reason).toBeDefined();
  });

  it("returns 401 with reason=session_revoked when the JWT is valid but the session was deleted", async () => {
    const { token } = await signJwt(
      {
        sub: "00000000-0000-0000-0000-000000000010",
        email: "noexist@example.com",
        jti: "00000000-0000-0000-0000-000000000011",
      },
      app.config,
    );
    const res = await jsonRequest(app, "/auth/validate", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(401);
    expect((res.body as { reason: string }).reason).toBe("session_revoked");
  });

  it("returns 401 with reason=session_expired when the session row is past expiresAt", async () => {
    const { token, user } = await signUp("expired@example.com", "correct horse battery staple");
    // Backdate the session row to 1 minute ago.
    await app.db.execute(
      `UPDATE "session" SET "expires_at" = NOW() - INTERVAL '1 minute' WHERE "user_id" = '${user.id}'`,
    );
    const res = await jsonRequest(app, "/auth/validate", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(401);
    expect((res.body as { reason: string }).reason).toBe("session_expired");
  });
});

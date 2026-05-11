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

async function signUp(email: string, password: string): Promise<{ token: string }> {
  const res = await jsonRequest(app, "/sign-up", { body: { email, password } });
  if (res.status !== 201) {
    throw new Error(`signup failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
  return res.body as { token: string };
}

describe("POST /sign-out", () => {
  it("revokes the session and a follow-up validate fails", async () => {
    const { token } = await signUp("signout@example.com", "correct horse battery staple");

    const res = await jsonRequest(app, "/sign-out", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ revoked: true });

    const validate = await jsonRequest(app, "/validate", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(validate.status).toBe(401);
    expect((validate.body as { reason: string }).reason).toBe("session_revoked");
  });

  it("rejects requests with no bearer token (401)", async () => {
    const res = await jsonRequest(app, "/sign-out");
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("missing_bearer_token");
  });

  it("rejects requests with an invalid token (401)", async () => {
    const res = await jsonRequest(app, "/sign-out", {
      headers: { authorization: "Bearer not-a-real-token" },
    });
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("invalid_token");
  });

  it("returns 404 when the JWT is valid but the session does not exist", async () => {
    // Sign a token whose jti points at a session that was never persisted.
    const { token } = await signJwt(
      {
        sub: "00000000-0000-0000-0000-000000000001",
        email: "ghost@example.com",
        role: "user",
        jti: "00000000-0000-0000-0000-000000000002",
      },
      app.config,
    );
    const res = await jsonRequest(app, "/sign-out", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(404);
    expect((res.body as { error: string }).error).toBe("session_not_found");
  });
});

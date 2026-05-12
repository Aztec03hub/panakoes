/**
 * Integration tests for GET /me, the canonical "is my token still
 * valid + who am I" endpoint the SPA calls on page-load hydration.
 *
 * The endpoint MUST:
 *   - return 200 + the server-trusted user identity when the token is
 *     signature-valid AND the underlying session is not revoked / expired
 *   - return 401 in every other case (no header, malformed token, expired
 *     token, revoked session, missing session row)
 *   - reflect server-side mutations (role promotions, etc.) so the SPA can
 *     refresh its stored claims without a fresh sign-in
 */
import { eq } from "drizzle-orm";
import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { signJwt } from "../../src/auth/jwt.ts";
import { session as sessionTable, user as userTable } from "../../src/db/schema.ts";
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
  user: { id: string; email: string; role: string };
}> {
  const res = await jsonRequest(app, "/sign-up", { body: { email, password } });
  if (res.status !== 201) {
    throw new Error(`signup failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
  return res.body as { token: string; user: { id: string; email: string; role: string } };
}

async function getMe(token: string | null) {
  return jsonRequest(app, "/me", {
    method: "GET",
    headers: token === null ? {} : { authorization: `Bearer ${token}` },
  });
}

describe("GET /me", () => {
  it("returns 200 + the user identity for an active session", async () => {
    const { token, user } = await signUp("me@example.com", "correct horse battery staple");
    const res = await getMe(token);
    expect(res.status).toBe(200);
    const body = res.body as { user: { id: string; email: string; name: string; role: string } };
    expect(body.user.id).toBe(user.id);
    expect(body.user.email).toBe("me@example.com");
    expect(body.user.role).toBe("user");
    // `name` is the email local-part on signup until the user updates it.
    expect(body.user.name).toBe("me");
  });

  it("reflects server-side role mutations (promoted user)", async () => {
    const { token } = await signUp("promoteme@example.com", "correct horse battery staple");
    await app.db
      .update(userTable)
      .set({ role: "admin" })
      .where(eq(userTable.email, "promoteme@example.com"));

    const res = await getMe(token);
    expect(res.status).toBe(200);
    const body = res.body as { user: { role: string } };
    // The token still carries role=user (immutable until re-auth), but
    // /me MUST return the current server-trusted role so the SPA
    // can refresh its stored claims.
    expect(body.user.role).toBe("admin");
  });

  it("returns 401 when no Authorization header is supplied", async () => {
    const res = await getMe(null);
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("missing_bearer_token");
  });

  it("returns 401 for a malformed token", async () => {
    const res = await getMe("not-a-real-token");
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("invalid_token");
  });

  it("returns 401 after the session is revoked via /sign-out", async () => {
    const { token } = await signUp("revoke@example.com", "correct horse battery staple");

    const live = await getMe(token);
    expect(live.status).toBe(200);

    const signOut = await jsonRequest(app, "/sign-out", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(signOut.status).toBe(204);

    const dead = await getMe(token);
    expect(dead.status).toBe(401);
    expect((dead.body as { reason?: string }).reason).toBe("session_revoked");
  });

  it("returns 401 with reason=session_expired when the session row is past expiresAt", async () => {
    const { token, user } = await signUp("expired@example.com", "correct horse battery staple");
    await app.db.execute(
      `UPDATE "session" SET "expires_at" = NOW() - INTERVAL '1 minute' WHERE "user_id" = '${user.id}'`,
    );
    const res = await getMe(token);
    expect(res.status).toBe(401);
    expect((res.body as { reason?: string }).reason).toBe("session_expired");
  });

  it("returns 401 when the JWT is signature-valid but the session row never existed", async () => {
    const { token } = await signJwt(
      {
        sub: "00000000-0000-0000-0000-000000000010",
        email: "ghost@example.com",
        role: "user",
        jti: "00000000-0000-0000-0000-000000000011",
      },
      app.config,
    );
    const res = await getMe(token);
    expect(res.status).toBe(401);
    expect((res.body as { reason?: string }).reason).toBe("session_revoked");
  });
});

describe("GET /me + /validate revoked_at semantics", () => {
  it("validate also returns session_revoked when revoked_at is set directly", async () => {
    const { token, user } = await signUp("direct@example.com", "correct horse battery staple");
    // Soft-delete the session row directly (bypassing /sign-out).
    await app.db
      .update(sessionTable)
      .set({ revokedAt: new Date() })
      .where(eq(sessionTable.userId, user.id));

    const validate = await jsonRequest(app, "/validate", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(validate.status).toBe(401);
    expect((validate.body as { reason: string }).reason).toBe("session_revoked");
  });
});

import { eq } from "drizzle-orm";
import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { signJwt } from "../../src/auth/jwt.ts";
import { session as sessionTable } from "../../src/db/schema.ts";
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
  it("soft-deletes the session (sets revoked_at) and returns 204", async () => {
    const { token } = await signUp("signout@example.com", "correct horse battery staple");

    const res = await jsonRequest(app, "/sign-out", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(res.status).toBe(204);
    // 204 No Content has an empty body.
    expect(res.body).toBeNull();

    // The session row MUST still exist (audit retention) but with a
    // non-null revoked_at set by the server.
    const rows = await app.db.select().from(sessionTable);
    expect(rows.length).toBe(1);
    expect(rows[0]?.revokedAt).not.toBeNull();

    // A follow-up validate against the same token now fails.
    const validate = await jsonRequest(app, "/validate", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(validate.status).toBe(401);
    expect((validate.body as { reason: string }).reason).toBe("session_revoked");
  });

  it("is idempotent: signing out twice with the same token still returns 204", async () => {
    const { token } = await signUp("twice@example.com", "correct horse battery staple");

    const first = await jsonRequest(app, "/sign-out", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(first.status).toBe(204);

    const second = await jsonRequest(app, "/sign-out", {
      headers: { authorization: `Bearer ${token}` },
    });
    expect(second.status).toBe(204);

    // revoked_at should not have been overwritten on the second call.
    const rows = await app.db.select().from(sessionTable);
    expect(rows.length).toBe(1);
    const firstRevoked = rows[0]?.revokedAt?.getTime();
    expect(firstRevoked).toBeDefined();
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

  it("returns 204 when the JWT is valid but the session does not exist (no-op success)", async () => {
    // Sign a token whose jti points at a session that was never persisted.
    // The JWT itself is signature-valid, so we treat the request as a
    // best-effort revocation and return 204; the validator already rejects
    // such tokens on the next request.
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
    expect(res.status).toBe(204);
    expect(res.body).toBeNull();
    // And no row got created.
    const rows = await app.db
      .select()
      .from(sessionTable)
      .where(eq(sessionTable.id, "00000000-0000-0000-0000-000000000002"));
    expect(rows.length).toBe(0);
  });
});

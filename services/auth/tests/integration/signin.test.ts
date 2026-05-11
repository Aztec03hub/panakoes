import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { verifyJwt } from "../../src/auth/jwt.ts";
import { buildTestApp, jsonRequest, truncateAll } from "../helpers.ts";

const app = buildTestApp();

beforeEach(async () => {
  await truncateAll(app.db);
});

afterAll(async () => {
  await app.cleanup();
});

async function ensureUser(email: string, password: string): Promise<void> {
  const res = await jsonRequest(app, "/sign-up", { body: { email, password } });
  if (res.status !== 201) {
    throw new Error(`signup precondition failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
}

describe("POST /sign-in", () => {
  it("returns a JWT for valid credentials", async () => {
    await ensureUser("signin@example.com", "correct horse battery staple");

    const res = await jsonRequest(app, "/sign-in", {
      body: { email: "signin@example.com", password: "correct horse battery staple" },
    });
    expect(res.status).toBe(200);
    const body = res.body as {
      token: string;
      user: { id: string; email: string; role: string };
    };
    expect(body.user.email).toBe("signin@example.com");
    expect(body.user.role).toBe("user");
    expect(typeof body.token).toBe("string");

    const verified = await verifyJwt(body.token, app.config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.role).toBe("user");
    }
  });

  it("embeds an admin role in the JWT when the user row was promoted", async () => {
    const email = "admin@example.com";
    await ensureUser(email, "correct horse battery staple");
    // Direct SQL promotion stands in for slice 2's role-management API.
    await app.db.execute(`UPDATE "user" SET "role" = 'admin' WHERE "email" = '${email}'`);

    const res = await jsonRequest(app, "/sign-in", {
      body: { email, password: "correct horse battery staple" },
    });
    expect(res.status).toBe(200);
    const body = res.body as { token: string; user: { role: string } };
    expect(body.user.role).toBe("admin");

    const verified = await verifyJwt(body.token, app.config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.role).toBe("admin");
    }
  });

  it("rejects wrong password with 401", async () => {
    await ensureUser("wrongpw@example.com", "correct horse battery staple");

    const res = await jsonRequest(app, "/sign-in", {
      body: { email: "wrongpw@example.com", password: "wrong wrong wrong" },
    });
    expect(res.status).toBe(401);
    expect((res.body as { error: string }).error).toBe("invalid_credentials");
  });

  it("rejects unknown email with 401", async () => {
    const res = await jsonRequest(app, "/sign-in", {
      body: { email: "ghost@example.com", password: "correct horse battery staple" },
    });
    expect(res.status).toBe(401);
  });

  it("rejects malformed payloads with 400", async () => {
    const res = await jsonRequest(app, "/sign-in", {
      body: { email: "not-email" },
    });
    expect(res.status).toBe(400);

    const noBody = await jsonRequest(app, "/sign-in", { body: undefined });
    expect(noBody.status).toBe(400);
  });
});

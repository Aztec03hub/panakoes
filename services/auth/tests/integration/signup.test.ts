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

describe("POST /sign-up", () => {
  it("creates a user and returns a JWT bound to a fresh session", async () => {
    const res = await jsonRequest(app, "/sign-up", {
      body: { email: "alice@example.com", password: "correct horse battery staple" },
    });
    expect(res.status).toBe(201);
    const body = res.body as {
      token: string;
      expiresAt: string;
      user: { id: string; email: string; role: string };
    };
    expect(body.user.email).toBe("alice@example.com");
    expect(body.user.id).toMatch(/[0-9a-f-]{36}/);
    expect(body.user.role).toBe("user");
    expect(typeof body.token).toBe("string");

    const verified = await verifyJwt(body.token, app.config);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.sub).toBe(body.user.id);
      expect(verified.claims.email).toBe("alice@example.com");
      expect(verified.claims.role).toBe("user");
      expect(verified.claims.jti).toMatch(/[0-9a-f-]{36}/);
    }
  });

  it("rejects malformed payloads with 400", async () => {
    const noEmail = await jsonRequest(app, "/sign-up", {
      body: { password: "correct horse battery staple" },
    });
    expect(noEmail.status).toBe(400);

    const badEmail = await jsonRequest(app, "/sign-up", {
      body: { email: "not-an-email", password: "correct horse battery staple" },
    });
    expect(badEmail.status).toBe(400);

    const shortPassword = await jsonRequest(app, "/sign-up", {
      body: { email: "bob@example.com", password: "short" },
    });
    expect(shortPassword.status).toBe(400);

    const noBody = await jsonRequest(app, "/sign-up", { body: undefined });
    expect(noBody.status).toBe(400);
  });

  it("returns 400 with reason=signup_failed when Better-Auth rejects the credentials", async () => {
    // 200-char password passes our zod (<= 256) but Better-Auth caps at 128.
    const longPassword = "A".repeat(200);
    const res = await jsonRequest(app, "/sign-up", {
      body: { email: "longpw@example.com", password: longPassword },
    });
    expect(res.status).toBe(400);
    expect((res.body as { error: string }).error).toBe("signup_failed");
  });

  it("rejects duplicate emails with 409", async () => {
    const first = await jsonRequest(app, "/sign-up", {
      body: { email: "dup@example.com", password: "correct horse battery staple" },
    });
    expect(first.status).toBe(201);

    const second = await jsonRequest(app, "/sign-up", {
      body: { email: "dup@example.com", password: "another long enough password" },
    });
    expect(second.status).toBe(409);
  });
});

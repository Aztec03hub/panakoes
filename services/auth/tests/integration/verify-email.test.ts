/**
 * Integration tests for the email-verification flow.
 *
 * Covers:
 *   - happy path: sign-up issues a verification email, GET /verify-email
 *     marks the user verified, the JWT issued on next sign-in carries
 *     `email_verified=true`, and the verification row is deleted.
 *   - expired tokens are rejected and garbage-collected.
 *   - invalid / missing tokens are rejected.
 *   - reusing a verified token is rejected (single-use semantics).
 *   - sign-up succeeds and the user is still created even when the email
 *     sender throws (best-effort dispatch).
 *   - the verification email body contains the canonical verify URL.
 *
 * v0.1 non-enforcement policy: sign-in is NOT blocked when the user is
 * unverified; the JWT claim simply reads `email_verified=false`. This is
 * an explicit pre-ADR decision (see services/auth/README.md).
 */
import { eq } from "drizzle-orm";
import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { createInMemoryEmailSender, renderVerificationEmail } from "../../src/auth/email.ts";
import { issueVerificationEmail } from "../../src/auth/verify-email.ts";
import { user as userTable, verification as verificationTable } from "../../src/db/schema.ts";
import { buildTestApp, jsonRequest, truncateAll } from "../helpers.ts";

const app = buildTestApp();

beforeEach(async () => {
  await truncateAll(app.db);
  // Reset the sent-email buffer between tests so each test sees only its own.
  app.sentEmails.length = 0;
});

afterAll(async () => {
  await app.cleanup();
});

async function signUp(email: string, password = "correct horse battery staple") {
  const res = await jsonRequest(app, "/sign-up", { body: { email, password } });
  if (res.status !== 201) {
    throw new Error(`signup failed: ${res.status} ${JSON.stringify(res.body)}`);
  }
  return res.body as {
    token: string;
    user: { id: string; email: string; role: string; email_verified: boolean };
  };
}

function tokenFromVerifyUrl(verifyUrl: string): string {
  const url = new URL(verifyUrl);
  const token = url.searchParams.get("token");
  if (!token) {
    throw new Error(`no token in url: ${verifyUrl}`);
  }
  return token;
}

describe("email verification on sign-up", () => {
  it("sends a verification email with a token and marks the new user unverified", async () => {
    const body = await signUp("happy@example.com");
    expect(body.user.email_verified).toBe(false);

    expect(app.sentEmails).toHaveLength(1);
    const email = app.sentEmails[0];
    expect(email).toBeDefined();
    if (!email) return;
    expect(email.to).toBe("happy@example.com");
    expect(email.verifyUrl).toMatch(
      /^https:\/\/api\.dev\.panakoes\.test\/v1\/auth\/verify-email\?token=[a-f0-9]{64}$/,
    );

    // The verification row is persisted with the same token value.
    const token = tokenFromVerifyUrl(email.verifyUrl);
    const rows = await app.db
      .select()
      .from(verificationTable)
      .where(eq(verificationTable.value, token));
    expect(rows).toHaveLength(1);
    expect(rows[0]?.identifier).toBe("happy@example.com");
  });

  it("encodes the JWT claim email_verified=false on the sign-up response", async () => {
    const body = await signUp("claim@example.com");
    const [, payload] = body.token.split(".");
    expect(payload).toBeDefined();
    if (!payload) return;
    const decoded = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    expect(decoded.email_verified).toBe(false);
  });
});

describe("GET /verify-email", () => {
  it("happy path: verifies the user, deletes the row, and the next sign-in carries email_verified=true", async () => {
    const signup = await signUp("verify@example.com");
    const verifyUrl = app.sentEmails[0]?.verifyUrl;
    expect(verifyUrl).toBeDefined();
    if (!verifyUrl) return;
    const token = tokenFromVerifyUrl(verifyUrl);

    const res = await jsonRequest(app, `/verify-email?token=${token}`, { method: "GET" });
    expect(res.status).toBe(200);
    expect(typeof res.body).toBe("string");
    expect(res.body as string).toContain("Email verified");

    // User row is flipped.
    const users = await app.db
      .select()
      .from(userTable)
      .where(eq(userTable.email, "verify@example.com"));
    expect(users[0]?.emailVerified).toBe(true);

    // Verification row is deleted.
    const verifications = await app.db
      .select()
      .from(verificationTable)
      .where(eq(verificationTable.value, token));
    expect(verifications).toHaveLength(0);

    // Sign-in now mints a token with email_verified=true.
    const signin = await jsonRequest(app, "/sign-in", {
      body: { email: "verify@example.com", password: "correct horse battery staple" },
    });
    expect(signin.status).toBe(200);
    const signinBody = signin.body as {
      token: string;
      user: { email_verified: boolean };
    };
    expect(signinBody.user.email_verified).toBe(true);
    const [, payload] = signinBody.token.split(".");
    expect(payload).toBeDefined();
    if (!payload) return;
    const decoded = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    expect(decoded.email_verified).toBe(true);

    // Original sign-up token is unaffected (the claim was minted before
    // verification, so it still reads false; client must re-sign-in or
    // re-fetch via /auth/me to get the fresh claim).
    void signup;
  });

  it("returns 400 + invalid HTML on a missing token query parameter", async () => {
    const res = await jsonRequest(app, "/verify-email", { method: "GET" });
    expect(res.status).toBe(400);
    expect(res.body as string).toContain("Link invalid or expired");
  });

  it("returns 400 + invalid HTML on a token that does not exist", async () => {
    const res = await jsonRequest(app, "/verify-email?token=bogus-token-not-in-db", {
      method: "GET",
    });
    expect(res.status).toBe(400);
    expect(res.body as string).toContain("Link invalid or expired");
  });

  it("returns 400 and garbage-collects the row when the token has expired", async () => {
    await signUp("expired@example.com");
    const verifyUrl = app.sentEmails[0]?.verifyUrl;
    expect(verifyUrl).toBeDefined();
    if (!verifyUrl) return;
    const token = tokenFromVerifyUrl(verifyUrl);

    // Force the row's expires_at into the past.
    await app.db
      .update(verificationTable)
      .set({ expiresAt: new Date(Date.now() - 1000) })
      .where(eq(verificationTable.value, token));

    const res = await jsonRequest(app, `/verify-email?token=${token}`, { method: "GET" });
    expect(res.status).toBe(400);
    expect(res.body as string).toContain("Link invalid or expired");

    // GC: expired row is deleted.
    const remaining = await app.db
      .select()
      .from(verificationTable)
      .where(eq(verificationTable.value, token));
    expect(remaining).toHaveLength(0);

    // User was NOT flipped to verified.
    const users = await app.db
      .select()
      .from(userTable)
      .where(eq(userTable.email, "expired@example.com"));
    expect(users[0]?.emailVerified).toBe(false);
  });

  it("rejects token replay (single-use semantics)", async () => {
    await signUp("replay@example.com");
    const verifyUrl = app.sentEmails[0]?.verifyUrl;
    expect(verifyUrl).toBeDefined();
    if (!verifyUrl) return;
    const token = tokenFromVerifyUrl(verifyUrl);

    const first = await jsonRequest(app, `/verify-email?token=${token}`, { method: "GET" });
    expect(first.status).toBe(200);

    const second = await jsonRequest(app, `/verify-email?token=${token}`, { method: "GET" });
    expect(second.status).toBe(400);
    expect(second.body as string).toContain("Link invalid or expired");
  });

  it("rejects an empty token query value", async () => {
    const res = await jsonRequest(app, "/verify-email?token=", { method: "GET" });
    expect(res.status).toBe(400);
  });
});

describe("sign-up email dispatch failure", () => {
  it("creates the user and returns 201 even when the email sender throws", async () => {
    // Build a one-off app whose email sender always throws so we can assert
    // the user creation still succeeds (best-effort dispatch).
    const { createDatabase } = await import("../../src/db/client.ts");
    const { createLogger } = await import("../../src/logger.ts");
    const { createServer } = await import("../../src/server.ts");
    const { testConfig } = await import("../helpers.ts");
    const config = testConfig();
    const { db, close } = createDatabase(config.DATABASE_URL);
    const logger = createLogger(config);
    const throwingSender = {
      async send() {
        throw new Error("simulated SES outage");
      },
    };
    const server = createServer({ db, config, logger, emailSender: throwingSender });
    try {
      // Truncate so this isolated app sees a clean state too.
      await truncateAll(db);
      const req = new Request("http://test.local/sign-up", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          email: "best-effort@example.com",
          password: "correct horse battery staple",
        }),
      });
      const res = await (server.fetch(req) as Promise<Response>);
      expect(res.status).toBe(201);
      const body = (await res.json()) as { user: { email: string; email_verified: boolean } };
      expect(body.user.email).toBe("best-effort@example.com");
      expect(body.user.email_verified).toBe(false);
    } finally {
      await close();
    }
  });
});

describe("issueVerificationEmail helper", () => {
  it("rejects when the sender promise rejects and propagates the error to the caller", async () => {
    const sender = {
      async send() {
        throw new Error("kaboom");
      },
    };
    // Create a user row first so the identifier is meaningful (not required
    // by the function itself but mirrors production usage).
    await app.db.insert(userTable).values({
      name: "h",
      email: "helper@example.com",
    });
    await expect(
      issueVerificationEmail("helper@example.com", {
        db: app.db,
        config: app.config,
        logger: { info() {}, warn() {}, error() {}, debug() {}, trace() {}, fatal() {} } as never,
        emailSender: sender,
      }),
    ).rejects.toThrow("kaboom");
  });

  it("renders email HTML and text with the expected verify URL", () => {
    const rendered = renderVerificationEmail("https://example.test/v1/auth/verify-email?token=abc");
    expect(rendered.subject).toBe("Verify your Panakoes email address");
    expect(rendered.text).toContain("https://example.test/v1/auth/verify-email?token=abc");
    expect(rendered.text).toContain("Welcome to Panakoes");
    expect(rendered.html).toContain("https://example.test/v1/auth/verify-email?token=abc");
    expect(rendered.html).toContain("Verify email");
  });

  it("in-memory sender captures send calls", async () => {
    const sender = createInMemoryEmailSender();
    await sender.send({ to: "a@b.com", verifyUrl: "https://x" });
    expect(sender.sent).toEqual([{ to: "a@b.com", verifyUrl: "https://x" }]);
  });
});

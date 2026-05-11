/**
 * Email verification token issuance + redemption.
 *
 * Tokens are 32 bytes from `crypto.randomBytes` rendered as hex (64 chars).
 * Stored in Better-Auth's `verification` table with:
 *   - `identifier` = user email (so we can correlate without joining)
 *   - `value`      = the random token
 *   - `expiresAt`  = now() + EMAIL_VERIFICATION_TTL_SECONDS (default 1h)
 *
 * Redemption (`GET /verify-email?token=`):
 *   - look the row up by token (`value`)
 *   - reject if missing OR if `expiresAt <= now()`
 *   - on success: flip `user.email_verified = true`, delete the row
 *     (single-use), return an HTML success page
 *   - on failure: return the same minimal HTML shell with a 400 status and
 *     an error message; never echo the token back into the HTML
 *
 * v0.1 non-enforcement: an unverified user can still sign in (the JWT just
 * carries `email_verified=false`). A future ADR-XX will flip this to deny.
 */
import { randomBytes } from "node:crypto";

import { eq, sql } from "drizzle-orm";
import { Hono } from "hono";

import type { Config } from "../config.ts";
import type { Database } from "../db/client.ts";
import { user as userTable, verification as verificationTable } from "../db/schema.ts";
import type { Logger } from "../logger.ts";
import type { VerificationEmailSender } from "./email.ts";

export interface VerifyEmailDeps {
  db: Database["db"];
  config: Config;
  logger: Logger;
  emailSender: VerificationEmailSender;
}

/**
 * Issue a verification token for `email`, persist it, and dispatch the
 * verification email. Returns the raw token so callers (tests, the sign-up
 * route) can correlate. Email-send failures are logged and surfaced to the
 * caller as a thrown error; the sign-up route catches and swallows so a
 * transient SES outage does not block account creation.
 */
export async function issueVerificationEmail(
  email: string,
  deps: VerifyEmailDeps,
): Promise<string> {
  const token = randomBytes(32).toString("hex");
  const ttlSeconds = deps.config.EMAIL_VERIFICATION_TTL_SECONDS;
  const expiresAt = new Date(Date.now() + ttlSeconds * 1000);

  await deps.db.insert(verificationTable).values({
    identifier: email,
    value: token,
    expiresAt,
  });

  const verifyUrl = `${deps.config.EMAIL_VERIFICATION_BASE_URL}?token=${encodeURIComponent(token)}`;
  await deps.emailSender.send({ to: email, verifyUrl });
  return token;
}

function htmlShell(title: string, body: string): string {
  return [
    "<!doctype html>",
    "<html><head>",
    '<meta charset="utf-8" />',
    `<title>${title}</title>`,
    '<meta name="viewport" content="width=device-width,initial-scale=1" />',
    "</head>",
    '<body style="font-family: system-ui, sans-serif; max-width: 480px; margin: 4rem auto; line-height: 1.5; color: #1a1a1a;">',
    body,
    "</body></html>",
  ].join("");
}

const SUCCESS_HTML = htmlShell(
  "Email verified",
  [
    '<h1 style="margin-bottom: 0.5rem;">Email verified</h1>',
    "<p>You can close this tab and sign in.</p>",
  ].join(""),
);

const INVALID_HTML = htmlShell(
  "Verification link invalid",
  [
    '<h1 style="margin-bottom: 0.5rem;">Link invalid or expired</h1>',
    "<p>This verification link is no longer valid. Sign in again to request a new one.</p>",
  ].join(""),
);

export function createVerifyEmailRoute(deps: VerifyEmailDeps): Hono {
  const { db, logger } = deps;
  const app = new Hono();

  app.get("/verify-email", async (c) => {
    const token = c.req.query("token");
    if (!token || typeof token !== "string" || token.length === 0) {
      return c.html(INVALID_HTML, 400);
    }

    const rows = await db
      .select()
      .from(verificationTable)
      .where(eq(verificationTable.value, token))
      .limit(1);
    const row = rows[0];

    if (!row) {
      return c.html(INVALID_HTML, 400);
    }

    if (row.expiresAt.getTime() <= Date.now()) {
      // Garbage-collect the expired row so the table does not balloon.
      await db.delete(verificationTable).where(eq(verificationTable.id, row.id));
      return c.html(INVALID_HTML, 400);
    }

    // Flip the user flag + delete the row in the same logical step. The
    // verification row is single-use; a replay with the same token after
    // this point lands on the "not found" branch above.
    await db
      .update(userTable)
      .set({ emailVerified: true, updatedAt: sql`now()` })
      .where(eq(userTable.email, row.identifier));
    await db.delete(verificationTable).where(eq(verificationTable.id, row.id));

    logger.info({ email: row.identifier }, "email verified");
    return c.html(SUCCESS_HTML, 200);
  });

  return app;
}

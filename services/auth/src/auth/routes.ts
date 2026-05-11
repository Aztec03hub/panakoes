/**
 * `/*` route handlers.
 *
 * Each handler validates input via zod, delegates the credential work to
 * Better-Auth, then mints a JWT bound to the new (or located) session. The
 * JWT carries the session UUID as `jti` so other services can call
 * `/validate` to check session-revocation freshness.
 */
import { and, desc, eq, isNull, sql } from "drizzle-orm";
import { Hono } from "hono";
import { z } from "zod";

import type { Config } from "../config.ts";
import type { Database } from "../db/client.ts";
import { session as sessionTable, type UserRole, user as userTable } from "../db/schema.ts";
import type { Logger } from "../logger.ts";
import type { AuthInstance } from "./better-auth.ts";
import type { VerificationEmailSender } from "./email.ts";
import { extractBearerToken, signJwt, verifyJwt } from "./jwt.ts";
import { issueVerificationEmail } from "./verify-email.ts";

const SignUpSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8).max(256),
});

const SignInSchema = z.object({
  email: z.string().email(),
  password: z.string().min(1).max(128),
});

export interface AuthRouteDeps {
  auth: AuthInstance;
  db: Database["db"];
  config: Config;
  logger: Logger;
  emailSender: VerificationEmailSender;
}

/**
 * Pick the most recent session row for a user. Better-Auth creates exactly
 * one fresh session on signup/signin, so the latest by `createdAt` is the
 * one we want to bind the JWT to.
 */
async function latestSessionForUser(
  db: Database["db"],
  userId: string,
): Promise<typeof sessionTable.$inferSelect | undefined> {
  const rows = await db
    .select()
    .from(sessionTable)
    .where(eq(sessionTable.userId, userId))
    .orderBy(desc(sessionTable.createdAt))
    .limit(1);
  return rows[0];
}

/**
 * Look up a user's RBAC role + email-verification status. Better-Auth's
 * signup/signin paths return the core user fields but not our custom `role`
 * column, so we re-query the user table by id. Defaults to `user` /
 * unverified if the row vanished between signup and the lookup (defensive;
 * should never happen in practice).
 */
async function userClaimsForUser(
  db: Database["db"],
  userId: string,
): Promise<{ role: UserRole; emailVerified: boolean }> {
  const rows = await db
    .select({ role: userTable.role, emailVerified: userTable.emailVerified })
    .from(userTable)
    .where(eq(userTable.id, userId))
    .limit(1);
  const row = rows[0];
  /* c8 ignore next -- defensive default; the row exists immediately after Better-Auth signup */
  return { role: row?.role ?? "user", emailVerified: row?.emailVerified ?? false };
}

export function createAuthRoutes(deps: AuthRouteDeps): Hono {
  const { auth, db, config, logger, emailSender } = deps;
  const app = new Hono();

  app.post("/sign-up", async (c) => {
    const json = await c.req.json().catch(() => null);
    const parsed = SignUpSchema.safeParse(json);
    if (!parsed.success) {
      return c.json({ error: "invalid_request", issues: parsed.error.issues }, 400);
    }

    try {
      // Better-Auth requires a name field; use the email local-part as a
      // sensible default. Slice 2 will accept a real name from the body.
      const localPart = parsed.data.email.split("@")[0];
      /* c8 ignore next -- localPart is always a string given zod-validated email */
      const name = localPart && localPart.length > 0 ? localPart : parsed.data.email;
      const result = await auth.api.signUpEmail({
        body: {
          email: parsed.data.email,
          password: parsed.data.password,
          name,
        },
        returnHeaders: false,
      });

      const sessionRow = await latestSessionForUser(db, result.user.id);
      /* c8 ignore start -- Better-Auth always creates a session on successful signup; defensive 500 */
      if (!sessionRow) {
        logger.error({ userId: result.user.id }, "signup completed but no session row found");
        return c.json({ error: "session_not_found" }, 500);
      }
      /* c8 ignore stop */

      const { role, emailVerified } = await userClaimsForUser(db, result.user.id);

      // Dispatch the verification email best-effort. SES outage or domain
      // misconfiguration MUST NOT block sign-up; the user can request a
      // fresh verification email later (slice 2 endpoint). The token row
      // still gets written even if the send fails, so a resend can reuse
      // the existing row if we choose to.
      try {
        await issueVerificationEmail(result.user.email, {
          db,
          config,
          logger,
          emailSender,
        });
      } catch (sendErr) {
        const sendMessage = sendErr instanceof Error ? sendErr.message : "unknown";
        logger.warn(
          { err: sendMessage, email: result.user.email },
          "verification email send failed; user created without dispatch",
        );
      }

      const signed = await signJwt(
        {
          sub: result.user.id,
          email: result.user.email,
          role,
          jti: sessionRow.id,
          email_verified: emailVerified,
        },
        config,
      );

      return c.json(
        {
          token: signed.token,
          expiresAt: signed.expiresAt.toISOString(),
          user: {
            id: result.user.id,
            email: result.user.email,
            role,
            email_verified: emailVerified,
          },
        },
        201,
      );
    } catch (err) {
      /* c8 ignore next -- Better-Auth always throws Error subclasses */
      const message = err instanceof Error ? err.message : "unknown";
      logger.warn({ err: message }, "sign-up failed");
      // Better-Auth surfaces duplicate emails by message; map to 409.
      // All other failures (invalid format passed our zod + Better-Auth still
      // rejects, internal error, etc.) get 400 with the surfaced message.
      const isConflict = /already|exist|unique|duplicate/i.test(message);
      return c.json(
        { error: isConflict ? "email_already_registered" : "signup_failed", message },
        isConflict ? 409 : 400,
      );
    }
  });

  app.post("/sign-in", async (c) => {
    const json = await c.req.json().catch(() => null);
    const parsed = SignInSchema.safeParse(json);
    if (!parsed.success) {
      return c.json({ error: "invalid_request", issues: parsed.error.issues }, 400);
    }

    try {
      const result = await auth.api.signInEmail({
        body: {
          email: parsed.data.email,
          password: parsed.data.password,
        },
        returnHeaders: false,
      });

      const sessionRow = await latestSessionForUser(db, result.user.id);
      /* c8 ignore start -- Better-Auth always creates a session on successful signin; defensive 500 */
      if (!sessionRow) {
        logger.error({ userId: result.user.id }, "signin completed but no session row found");
        return c.json({ error: "session_not_found" }, 500);
      }
      /* c8 ignore stop */

      const { role, emailVerified } = await userClaimsForUser(db, result.user.id);
      const signed = await signJwt(
        {
          sub: result.user.id,
          email: result.user.email,
          role,
          jti: sessionRow.id,
          email_verified: emailVerified,
        },
        config,
      );

      return c.json({
        token: signed.token,
        expiresAt: signed.expiresAt.toISOString(),
        user: {
          id: result.user.id,
          email: result.user.email,
          role,
          email_verified: emailVerified,
        },
      });
    } catch (err) {
      /* c8 ignore next -- Better-Auth always throws Error subclasses */
      const message = err instanceof Error ? err.message : "unknown";
      logger.info({ err: message }, "sign-in failed");
      return c.json({ error: "invalid_credentials" }, 401);
    }
  });

  app.post("/sign-out", async (c) => {
    const token = extractBearerToken(c.req.header("authorization"));
    if (!token) {
      return c.json({ error: "missing_bearer_token" }, 401);
    }

    const result = await verifyJwt(token, config);
    if (!result.ok) {
      // Idempotent UX: an already-invalid token is treated as 401 rather
      // than 204 so the client can distinguish "you weren't signed in to
      // begin with" from "your session has been revoked".
      return c.json({ error: "invalid_token" }, 401);
    }

    // Soft-delete: only mark a live session as revoked. If the row was
    // already revoked we treat the request as a no-op-but-success (the
    // user's intent is satisfied either way) and still return 204 below.
    const updated = await db
      .update(sessionTable)
      .set({ revokedAt: sql`now()` })
      .where(and(eq(sessionTable.id, result.claims.jti), isNull(sessionTable.revokedAt)))
      .returning({ id: sessionTable.id });

    // If no row matched, the session either never existed (forged jti) or
    // was already revoked. The token was JWT-valid, so we accept the
    // sign-out as a no-op and still return 204 to keep the client flow
    // idempotent. The validator already rejects revoked / missing sessions
    // on the next request, so there's no security loss.
    void updated;

    return c.body(null, 204);
  });

  app.get("/auth/me", async (c) => {
    const token = extractBearerToken(c.req.header("authorization"));
    if (!token) {
      return c.json({ error: "missing_bearer_token" }, 401);
    }

    const result = await verifyJwt(token, config);
    if (!result.ok) {
      return c.json({ error: "invalid_token", reason: result.error.kind }, 401);
    }

    // Whoami is the canonical "is my token still valid" endpoint for the
    // SPA. We MUST consult the session table here so a revoked-but-not-yet
    // -expired token returns 401 immediately. We also re-read the user row
    // so the server-trusted role + name claims reflect any server-side
    // mutation that happened after the JWT was minted (role promotion,
    // name change, etc.).
    const rows = await db
      .select({
        sessionId: sessionTable.id,
        sessionExpiresAt: sessionTable.expiresAt,
        sessionRevokedAt: sessionTable.revokedAt,
        userId: userTable.id,
        userEmail: userTable.email,
        userName: userTable.name,
        userRole: userTable.role,
        userEmailVerified: userTable.emailVerified,
      })
      .from(sessionTable)
      .innerJoin(userTable, eq(userTable.id, sessionTable.userId))
      .where(eq(sessionTable.id, result.claims.jti))
      .limit(1);
    const row = rows[0];

    if (!row) {
      return c.json({ error: "invalid_token", reason: "session_revoked" }, 401);
    }
    if (row.sessionRevokedAt !== null) {
      return c.json({ error: "invalid_token", reason: "session_revoked" }, 401);
    }
    if (row.sessionExpiresAt.getTime() <= Date.now()) {
      return c.json({ error: "invalid_token", reason: "session_expired" }, 401);
    }

    return c.json({
      user: {
        id: row.userId,
        email: row.userEmail,
        name: row.userName,
        role: row.userRole,
        email_verified: row.userEmailVerified,
      },
    });
  });

  return app;
}

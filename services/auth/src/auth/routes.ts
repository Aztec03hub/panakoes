/**
 * `/auth/*` route handlers.
 *
 * Each handler validates input via zod, delegates the credential work to
 * Better-Auth, then mints a JWT bound to the new (or located) session. The
 * JWT carries the session UUID as `jti` so other services can call
 * `/auth/validate` to check session-revocation freshness.
 */
import { desc, eq } from "drizzle-orm";
import { Hono } from "hono";
import { z } from "zod";

import type { Config } from "../config.ts";
import type { Database } from "../db/client.ts";
import { session as sessionTable, type UserRole, user as userTable } from "../db/schema.ts";
import type { Logger } from "../logger.ts";
import type { AuthInstance } from "./better-auth.ts";
import { extractBearerToken, signJwt, verifyJwt } from "./jwt.ts";

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
 * Look up a user's role. Better-Auth's signup/signin paths return the
 * core user fields but not our custom RBAC `role` column, so we re-query
 * the user table by id. Defaults to `user` if the row vanished between
 * signup and the lookup (defensive; should never happen in practice).
 */
async function roleForUser(db: Database["db"], userId: string): Promise<UserRole> {
  const rows = await db
    .select({ role: userTable.role })
    .from(userTable)
    .where(eq(userTable.id, userId))
    .limit(1);
  /* c8 ignore next -- defensive default; the row exists immediately after Better-Auth signup */
  return rows[0]?.role ?? "user";
}

export function createAuthRoutes(deps: AuthRouteDeps): Hono {
  const { auth, db, config, logger } = deps;
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

      const role = await roleForUser(db, result.user.id);
      const signed = await signJwt(
        { sub: result.user.id, email: result.user.email, role, jti: sessionRow.id },
        config,
      );

      return c.json(
        {
          token: signed.token,
          expiresAt: signed.expiresAt.toISOString(),
          user: { id: result.user.id, email: result.user.email, role },
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

      const role = await roleForUser(db, result.user.id);
      const signed = await signJwt(
        { sub: result.user.id, email: result.user.email, role, jti: sessionRow.id },
        config,
      );

      return c.json({
        token: signed.token,
        expiresAt: signed.expiresAt.toISOString(),
        user: { id: result.user.id, email: result.user.email, role },
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
      return c.json({ error: "invalid_token" }, 401);
    }

    const deleted = await db
      .delete(sessionTable)
      .where(eq(sessionTable.id, result.claims.jti))
      .returning({ id: sessionTable.id });

    if (deleted.length === 0) {
      return c.json({ error: "session_not_found" }, 404);
    }

    return c.json({ revoked: true });
  });

  return app;
}

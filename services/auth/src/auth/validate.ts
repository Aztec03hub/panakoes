/**
 * `/auth/validate` route.
 *
 * Other services call this to confirm a JWT is signature-valid AND its
 * underlying session has not been revoked. JWT-only validation is acceptable
 * for stateless services that tolerate up-to-1-hour staleness; services
 * needing fresh revocation status hit this endpoint per request.
 */
import { eq } from "drizzle-orm";
import { Hono } from "hono";

import type { Config } from "../config.ts";
import type { Database } from "../db/client.ts";
import { session as sessionTable } from "../db/schema.ts";
import { extractBearerToken, verifyJwt } from "./jwt.ts";

export interface ValidateRouteDeps {
  db: Database["db"];
  config: Config;
}

export function createValidateRoute(deps: ValidateRouteDeps): Hono {
  const { db, config } = deps;
  const app = new Hono();

  app.post("/validate", async (c) => {
    const token = extractBearerToken(c.req.header("authorization"));
    if (!token) {
      return c.json({ valid: false, reason: "missing_bearer_token" }, 401);
    }

    const result = await verifyJwt(token, config);
    if (!result.ok) {
      return c.json({ valid: false, reason: result.error.kind }, 401);
    }

    const rows = await db
      .select()
      .from(sessionTable)
      .where(eq(sessionTable.id, result.claims.jti))
      .limit(1);
    const row = rows[0];

    if (!row) {
      return c.json({ valid: false, reason: "session_revoked" }, 401);
    }
    if (row.expiresAt.getTime() <= Date.now()) {
      return c.json({ valid: false, reason: "session_expired" }, 401);
    }

    return c.json({
      valid: true,
      user: { id: result.claims.sub, email: result.claims.email },
    });
  });

  return app;
}

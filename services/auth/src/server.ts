/**
 * Hono app construction.
 *
 * Composes the health, Better-Auth handler, custom auth, and validate
 * sub-apps. `index.ts` is responsible for binding the assembled app to a
 * port; this module is import-safe (no side effects) so tests can spin up
 * the app against a testcontainers Postgres without touching `process.env`.
 */
import { Hono } from "hono";

import { createAuth } from "./auth/better-auth.ts";
import { createAuthRoutes } from "./auth/routes.ts";
import { createValidateRoute } from "./auth/validate.ts";
import type { Config } from "./config.ts";
import type { Database } from "./db/client.ts";
import { createHealthRoutes } from "./health/routes.ts";
import type { Logger } from "./logger.ts";

export interface ServerDeps {
  db: Database["db"];
  config: Config;
  logger: Logger;
}

export function createServer(deps: ServerDeps): Hono {
  const { db, config, logger } = deps;
  const auth = createAuth(db, config);

  const app = new Hono();

  app.route("/", createHealthRoutes());

  // Better-Auth's own handler (catches /api/auth/* for direct flows like
  // /api/auth/get-session, /api/auth/sign-out, etc.). Our spec'd endpoints
  // live under /auth/* so we never collide.
  app.on(["GET", "POST"], "/api/auth/*", (c) => auth.handler(c.req.raw));

  app.route("/auth", createAuthRoutes({ auth, db, config, logger }));
  app.route("/auth", createValidateRoute({ db, config }));

  return app;
}

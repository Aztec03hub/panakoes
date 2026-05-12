/**
 * Hono app construction.
 *
 * Composes the health, Better-Auth handler, custom auth, validate, MFA,
 * and JWKS sub-apps. `index.ts` is responsible for binding the assembled
 * app to a port; this module is import-safe (no side effects) so tests
 * can spin up the app against a testcontainers Postgres without touching
 * `process.env`.
 */
import { Hono } from "hono";
import { cors } from "hono/cors";

import { createAuth } from "./auth/better-auth.ts";
import { createJwksRoute } from "./auth/jwks.ts";
import type { KmsSigner } from "./auth/kms-signer.ts";
import { createMfaRoutes } from "./auth/mfa-routes.ts";
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
  /**
   * KMS-backed signer for RS256. Required when
   * `config.AUTH_JWT_ALGORITHM === "RS256"`; ignored on the HS256 path.
   * Injected from `index.ts` (production) or left out for tests that
   * exercise the HS256 default.
   */
  kmsSigner?: KmsSigner;
}

export function createServer(deps: ServerDeps): Hono {
  const { db, config, logger, kmsSigner } = deps;
  const auth = createAuth(db, config);

  const app = new Hono();

  // CORS preflight handling. The API Gateway HTTP API's auto-CORS only
  // fires when no route matches the request; our `ANY /v1/auth/{proxy+}`
  // catches OPTIONS too and forwards it to this backend, so the backend
  // itself must answer preflights. Hono's cors() middleware short-circuits
  // OPTIONS to 204 with the right Access-Control-* headers before any
  // route handler runs.
  app.use(
    "*",
    cors({
      origin: [
        "https://dmaopcm3hnxog.cloudfront.net",
        "https://lafayettelabs.com",
        "https://panakoes.com",
        "http://localhost:5173",
      ],
      allowMethods: ["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
      allowHeaders: ["authorization", "content-type", "x-request-id"],
      credentials: true,
      maxAge: 600,
    }),
  );

  app.route("/", createHealthRoutes());
  app.route("/", createJwksRoute({ config, kmsSigner }));

  // Better-Auth's own handler (catches /api/auth/* for direct flows like
  // /api/auth/get-session, /api/auth/sign-out, etc.). Kept at /api/auth/*
  // per Better-Auth SDK convention.
  app.on(["GET", "POST"], "/api/auth/*", (c) => auth.handler(c.req.raw));

  // Custom routes mount at root so the api-gateway proxy-with-overrides
  // shape forwards /v1/auth/{proxy+} -> /<proxy> cleanly. Public callers
  // see /v1/auth/sign-up; the gateway strips /v1/auth and the backend
  // sees /sign-up.
  app.route("/", createAuthRoutes({ auth, db, config, logger, kmsSigner }));
  app.route("/", createValidateRoute({ db, config, kmsSigner }));
  app.route("/", createMfaRoutes({ config, logger, kmsSigner }));

  return app;
}

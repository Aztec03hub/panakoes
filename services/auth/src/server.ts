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

import { createAuth } from "./auth/better-auth.ts";
import {
  createInMemoryEmailSender,
  createSesEmailSender,
  type VerificationEmailSender,
} from "./auth/email.ts";
import { createJwksRoute } from "./auth/jwks.ts";
import { createMfaRoutes } from "./auth/mfa-routes.ts";
import { createAuthRoutes } from "./auth/routes.ts";
import { createValidateRoute } from "./auth/validate.ts";
import { createVerifyEmailRoute } from "./auth/verify-email.ts";
import type { Config } from "./config.ts";
import type { Database } from "./db/client.ts";
import { createHealthRoutes } from "./health/routes.ts";
import type { Logger } from "./logger.ts";

export interface ServerDeps {
  db: Database["db"];
  config: Config;
  logger: Logger;
  /**
   * Optional override for the verification-email sender. Tests inject an
   * in-memory capturing sender; production omits this and gets the SES
   * client based on `config.EMAIL_SENDER_MODE`.
   */
  emailSender?: VerificationEmailSender;
}

/* c8 ignore start -- production wiring exercised by buildTestApp injection in tests; the SES branch requires real AWS credentials and the disabled branch is only hit when buildTestApp does NOT pass an explicit emailSender (it always does) */
function defaultEmailSender(config: Config): VerificationEmailSender {
  if (config.EMAIL_SENDER_MODE === "ses") {
    return createSesEmailSender({
      region: config.SES_REGION,
      fromAddress: config.SES_FROM_ADDRESS,
      replyToAddress: config.SES_REPLY_TO_ADDRESS,
    });
  }
  return createInMemoryEmailSender();
}
/* c8 ignore stop */

export function createServer(deps: ServerDeps): Hono {
  const { db, config, logger } = deps;
  const auth = createAuth(db, config);
  const emailSender =
    /* c8 ignore next -- defaultEmailSender is production-only; tests always inject */
    deps.emailSender ?? defaultEmailSender(config);

  const app = new Hono();

  app.route("/", createHealthRoutes());
  app.route("/", createJwksRoute());

  // Better-Auth's own handler (catches /api/auth/* for direct flows like
  // /api/auth/get-session, /api/auth/sign-out, etc.). Kept at /api/auth/*
  // per Better-Auth SDK convention.
  app.on(["GET", "POST"], "/api/auth/*", (c) => auth.handler(c.req.raw));

  // Custom routes mount at root so the api-gateway proxy-with-overrides
  // shape forwards /v1/auth/{proxy+} -> /<proxy> cleanly. Public callers
  // see /v1/auth/sign-up; the gateway strips /v1/auth and the backend
  // sees /sign-up.
  app.route("/", createAuthRoutes({ auth, db, config, logger, emailSender }));
  app.route("/", createValidateRoute({ db, config }));
  app.route("/", createMfaRoutes({ config, logger }));
  app.route("/", createVerifyEmailRoute({ db, config, logger, emailSender }));

  return app;
}

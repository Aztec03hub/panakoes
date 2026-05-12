/**
 * `/auth/mfa/*` step-up MFA routes.
 *
 * SCOPE NOTE (slice 1 stub): this is a wire-shape implementation. Real
 * persistence of TOTP secrets, the rate-limiter on `/verify`, and the
 * full Tier 3 admin-route policy land in slice 2. The routes below
 * exercise the `enrollMfa` / `verifyMfaCode` / `signStepUpToken` paths
 * end-to-end so the Tier 3 contract is testable today.
 *
 * Endpoints:
 *  - POST `/mfa/enroll` (admin only): issues a fresh TOTP secret +
 *    `otpauth://` URI; the client renders the URI as a QR code and
 *    persists the secret until verify.
 *  - POST `/mfa/verify`: body `{code, secret_key}`; on success
 *    issues a 5-minute step-up token. The body carries the `secret_key`
 *    until slice 2 lands persistence (this matches the documented stub
 *    contract; never use this shape with real client code).
 *  - POST `/mfa/challenge`: gate that returns 401 + `WWW-Authenticate:
 *    StepUp` if the request lacks a valid step-up token. Tier 3 admin
 *    routes call this (or import the same logic) before allowing the
 *    sensitive action.
 */
import { Hono } from "hono";
import { z } from "zod";

import type { Config } from "../config.ts";
import type { Logger } from "../logger.ts";
import { extractBearerToken, signStepUpToken, verifyJwt, verifyStepUpToken } from "./jwt.ts";
import type { KmsSigner } from "./kms-signer.ts";
import { enrollMfa, verifyMfaCode } from "./mfa.ts";

const VerifySchema = z.object({
  code: z.string().regex(/^\d{6}$/u, "code must be a 6-digit string"),
  secret_key: z.string().min(1, "secret_key is required for the slice-1 stub"),
});

export interface MfaRouteDeps {
  config: Config;
  logger: Logger;
  kmsSigner?: KmsSigner;
}

export function createMfaRoutes(deps: MfaRouteDeps): Hono {
  const { config, logger, kmsSigner } = deps;
  const app = new Hono();

  app.post("/mfa/enroll", async (c) => {
    const token = extractBearerToken(c.req.header("authorization"));
    if (!token) {
      return c.json({ error: "missing_bearer_token" }, 401);
    }
    const result = await verifyJwt(token, config, kmsSigner);
    if (!result.ok) {
      return c.json({ error: "invalid_token", reason: result.error.kind }, 401);
    }
    if (result.claims.role !== "admin") {
      return c.json({ error: "forbidden", reason: "admin_role_required" }, 403);
    }

    const offer = enrollMfa({ accountLabel: result.claims.email });
    logger.info({ userId: result.claims.sub }, "mfa enrollment offered (stub)");
    return c.json(offer);
  });

  app.post("/mfa/verify", async (c) => {
    const token = extractBearerToken(c.req.header("authorization"));
    if (!token) {
      return c.json({ error: "missing_bearer_token" }, 401);
    }
    const result = await verifyJwt(token, config, kmsSigner);
    if (!result.ok) {
      return c.json({ error: "invalid_token", reason: result.error.kind }, 401);
    }

    const json = await c.req.json().catch(() => null);
    const parsed = VerifySchema.safeParse(json);
    if (!parsed.success) {
      return c.json({ error: "invalid_request", issues: parsed.error.issues }, 400);
    }

    const verify = verifyMfaCode(parsed.data.code, parsed.data.secret_key);
    if (!verify.ok) {
      return c.json({ error: "invalid_mfa_code", reason: verify.reason }, 401);
    }

    const stepUp = await signStepUpToken(
      {
        sub: result.claims.sub,
        email: result.claims.email,
        role: result.claims.role,
      },
      config,
      kmsSigner,
    );

    return c.json({
      step_up_token: stepUp.token,
      expiresAt: stepUp.expiresAt.toISOString(),
    });
  });

  app.post("/mfa/challenge", async (c) => {
    const token = extractBearerToken(c.req.header("authorization"));
    if (!token) {
      c.header("WWW-Authenticate", "StepUp");
      return c.json({ error: "step_up_required" }, 401);
    }
    const result = await verifyStepUpToken(token, config, kmsSigner);
    if (!result.ok) {
      c.header("WWW-Authenticate", "StepUp");
      return c.json({ error: "step_up_required", reason: result.error.kind }, 401);
    }
    return c.json({
      step_up: true,
      user: {
        id: result.claims.sub,
        email: result.claims.email,
        role: result.claims.role,
      },
    });
  });

  return app;
}

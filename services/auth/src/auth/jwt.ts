/**
 * HS256 JWT signing and verification using `jose`.
 *
 * Locked v0.1 decision: HS256 with shared secret. RS256 + JWKS migration is
 * slice 2 (PLANNING.md ADR-005, ADR-022), before any second service consumes
 * JWTs in non-trusted contexts.
 *
 * Payload shape: `{ sub: user_id, email, role, iat, exp, jti: session_id }`.
 * Issuer and audience are claim-validated on every verification.
 *
 * Step-up tokens are a separate token class minted post-MFA-verify; they
 * carry `step_up: true` and a tight 5-minute exp, and they are NOT a
 * substitute for a session-bound access token (no `jti` is required).
 */
import { createPublicKey, type KeyObject } from "node:crypto";

import { errors as joseErrors, jwtVerify, SignJWT } from "jose";

import type { Config } from "../config.ts";
import { isUserRole, type UserRole } from "../db/schema.ts";
import type { KmsSigner } from "./kms-signer.ts";

/**
 * Plan values the billing service projects onto each tenant. Free is the
 * implicit default for any tenant without a Stripe subscription on file;
 * `pro` and `team` map to the two paid Stripe Price IDs. Downstream
 * services use `require_plan("pro")` (panakoes-middleware) to 402 callers
 * whose JWT plan claim ranks below a route's minimum.
 */
export const PLANS = ["free", "pro", "team"] as const;
export type Plan = (typeof PLANS)[number];

export function isPlan(value: unknown): value is Plan {
  return typeof value === "string" && (PLANS as readonly string[]).includes(value);
}

export interface JwtClaims {
  sub: string;
  email: string;
  role: UserRole;
  jti: string;
  /**
   * The tenant's current billing plan, baked in at sign-in time. Stays
   * stable for the JWT's lifetime; a plan upgrade or downgrade applied
   * via a Stripe webhook becomes visible the next time the user signs in
   * (or when a `/validate` round-trip refreshes the claims). Optional on
   * the input shape so existing callers (pre-billing-webhooks slice)
   * compile unchanged; the signer defaults a missing value to "free".
   */
  plan?: Plan;
}

export interface SignedToken {
  token: string;
  expiresAt: Date;
}

export interface VerifiedJwt extends Omit<JwtClaims, "plan"> {
  iat: number;
  exp: number;
  /** Always populated on verified tokens; defaults to "free" if absent. */
  plan: Plan;
}

export type JwtConfig = Pick<
  Config,
  | "AUTH_JWT_SECRET"
  | "AUTH_JWT_ISSUER"
  | "AUTH_JWT_AUDIENCE"
  | "AUTH_JWT_EXPIRES_IN_SECONDS"
  | "AUTH_JWT_ALGORITHM"
>;

/**
 * Verification error categories. We map all `jose` failures into these so
 * route handlers can produce stable response shapes without leaking
 * library-specific error messages to clients.
 */
export type JwtVerifyError =
  | { kind: "expired" }
  | { kind: "invalid_signature" }
  | { kind: "claim_mismatch"; reason: string }
  | { kind: "malformed" };

export type JwtVerifyResult =
  | { ok: true; claims: VerifiedJwt }
  | { ok: false; error: JwtVerifyError };

/** Step-up tokens carry the same identity claims plus `step_up: true`. */
export interface StepUpClaims {
  sub: string;
  email: string;
  role: UserRole;
}

export interface VerifiedStepUp extends StepUpClaims {
  iat: number;
  exp: number;
  step_up: true;
}

export type StepUpVerifyResult =
  | { ok: true; claims: VerifiedStepUp }
  | { ok: false; error: JwtVerifyError };

/**
 * Step-up token expiry window. 5 minutes is short enough to limit the blast
 * radius of a leaked step-up token while still letting an admin chain a few
 * Tier 3 calls (e.g. open IAM, change a user's role, sign out).
 */
export const STEP_UP_EXPIRES_IN_SECONDS = 300;

function secretBytes(config: JwtConfig): Uint8Array {
  return new TextEncoder().encode(config.AUTH_JWT_SECRET);
}

/**
 * Resolve the verification key + accepted algorithms based on the
 * configured signing algorithm. HS256 uses the local secret; RS256
 * pulls the JWKS from KMS, picks the entry that matches the configured
 * `kid`, and materialises it into a Node `KeyObject` jose can verify
 * against. The KMS signer caches the public key, so this is a single
 * outbound network call at boot and a cache hit thereafter.
 */
async function resolveVerificationKey(
  config: JwtConfig,
  kmsSigner: KmsSigner | undefined,
): Promise<{ key: Uint8Array | KeyObject; algorithms: ("HS256" | "RS256")[] }> {
  if (config.AUTH_JWT_ALGORITHM === "RS256") {
    if (!kmsSigner) {
      throw new Error("RS256 verification requires a KmsSigner; check server.ts wiring");
    }
    const jwks = await kmsSigner.getJwks();
    const jwk = jwks.keys[0];
    /* c8 ignore next 3 -- defensive: KMS always returns exactly one key for SIGN_VERIFY */
    if (!jwk) {
      throw new Error("KMS JWKS document had no keys; cannot verify RS256 token");
    }
    // `createPublicKey` accepts a JsonWebKey-shaped object; our JwksKey
    // satisfies that shape but lacks the open index signature TypeScript
    // requires, so we widen via a structured shallow copy.
    const jwkAsJson: Record<string, string> = {
      kty: jwk.kty,
      n: jwk.n,
      e: jwk.e,
      alg: jwk.alg,
      use: jwk.use,
      kid: jwk.kid,
    };
    const key = createPublicKey({ key: jwkAsJson, format: "jwk" });
    return { key, algorithms: ["RS256"] };
  }
  return { key: secretBytes(config), algorithms: ["HS256"] };
}

/**
 * Mint a JWT for a freshly-authenticated session.
 *
 * - `sub` is the user UUID.
 * - `jti` is the session UUID (used for revocation checks).
 * - `role` is the RBAC role baked into the token so downstream services can
 *   authorize without a per-request `/validate` round-trip.
 * - `iat` / `exp` are derived from the configured expiry window.
 */
export async function signJwt(
  claims: JwtClaims,
  config: JwtConfig,
  kmsSigner?: KmsSigner,
): Promise<SignedToken> {
  const issuedAt = Math.floor(Date.now() / 1000);
  const expiresAt = issuedAt + config.AUTH_JWT_EXPIRES_IN_SECONDS;

  if (config.AUTH_JWT_ALGORITHM === "RS256") {
    if (!kmsSigner) {
      throw new Error("RS256 signing requires a KmsSigner; check server.ts wiring");
    }
    const token = await signRs256Compact({
      payload: {
        email: claims.email,
        role: claims.role,
        plan: claims.plan ?? "free",
        sub: claims.sub,
        jti: claims.jti,
        iss: config.AUTH_JWT_ISSUER,
        aud: config.AUTH_JWT_AUDIENCE,
        iat: issuedAt,
        exp: expiresAt,
      },
      kmsSigner,
    });
    return { token, expiresAt: new Date(expiresAt * 1000) };
  }

  const token = await new SignJWT({
    email: claims.email,
    role: claims.role,
    plan: claims.plan ?? "free",
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(claims.sub)
    .setJti(claims.jti)
    .setIssuer(config.AUTH_JWT_ISSUER)
    .setAudience(config.AUTH_JWT_AUDIENCE)
    .setIssuedAt(issuedAt)
    .setExpirationTime(expiresAt)
    .sign(secretBytes(config));

  return { token, expiresAt: new Date(expiresAt * 1000) };
}

/**
 * Build a compact-serialised RS256 JWS by hand. We avoid jose's high-level
 * `SignJWT` because it expects a local private key; with KMS the signing
 * happens out-of-process and we only get back the signature bytes.
 *
 * Header includes `kid` so JWKS consumers can match the verifying key
 * without fetching every key in the document. The header + payload are
 * base64url-JSON-encoded, joined by a dot, and the KMS-returned
 * signature is appended after another dot.
 */
async function signRs256Compact(args: {
  payload: Record<string, unknown>;
  kmsSigner: KmsSigner;
}): Promise<string> {
  const kid = await args.kmsSigner.kid();
  const header = { alg: "RS256", typ: "JWT", kid };
  const encodedHeader = Buffer.from(JSON.stringify(header)).toString("base64url");
  const encodedPayload = Buffer.from(JSON.stringify(args.payload)).toString("base64url");
  const signingInput = `${encodedHeader}.${encodedPayload}`;
  const signature = await args.kmsSigner.sign(signingInput);
  return `${signingInput}.${signature}`;
}

/**
 * Verify a token's signature, expiry, issuer, and audience. Returns a
 * tagged-union result so callers can branch on error category without
 * try/catch noise.
 */
export async function verifyJwt(
  token: string,
  config: JwtConfig,
  kmsSigner?: KmsSigner,
): Promise<JwtVerifyResult> {
  try {
    const { key, algorithms } = await resolveVerificationKey(config, kmsSigner);
    const { payload } = await jwtVerify(token, key, {
      issuer: config.AUTH_JWT_ISSUER,
      audience: config.AUTH_JWT_AUDIENCE,
      algorithms,
    });

    if (
      typeof payload.sub !== "string" ||
      typeof payload.email !== "string" ||
      typeof payload.jti !== "string" ||
      typeof payload.iat !== "number" ||
      typeof payload.exp !== "number"
    ) {
      return { ok: false, error: { kind: "claim_mismatch", reason: "missing required claim" } };
    }

    if (!isUserRole(payload.role)) {
      return { ok: false, error: { kind: "claim_mismatch", reason: "invalid role" } };
    }

    // The plan claim defaults to "free" when missing. A missing plan must
    // never accidentally upgrade the caller; the worst case here is the
    // tenant pays for pro but the JWT was minted before that PR landed,
    // in which case the next sign-in produces a correct claim.
    const plan: Plan = isPlan(payload.plan) ? payload.plan : "free";

    return {
      ok: true,
      claims: {
        sub: payload.sub,
        email: payload.email,
        role: payload.role,
        jti: payload.jti,
        plan,
        iat: payload.iat,
        exp: payload.exp,
      },
    };
  } catch (err) {
    if (err instanceof joseErrors.JWTExpired) {
      return { ok: false, error: { kind: "expired" } };
    }
    if (err instanceof joseErrors.JWSSignatureVerificationFailed) {
      return { ok: false, error: { kind: "invalid_signature" } };
    }
    if (err instanceof joseErrors.JWTClaimValidationFailed) {
      /* c8 ignore next -- jose always populates `claim` on this error class */
      const reason = err.claim || "unknown";
      return { ok: false, error: { kind: "claim_mismatch", reason } };
    }
    return { ok: false, error: { kind: "malformed" } };
  }
}

/**
 * Mint a short-lived step-up token (5 minutes) after a successful MFA
 * challenge. Carries `step_up: true` so verifiers can distinguish it from
 * a regular access token. Step-up tokens never replace the access token;
 * they sit alongside it and gate Tier 3 admin routes.
 */
export async function signStepUpToken(
  claims: StepUpClaims,
  config: JwtConfig,
  kmsSigner?: KmsSigner,
): Promise<SignedToken> {
  const issuedAt = Math.floor(Date.now() / 1000);
  const expiresAt = issuedAt + STEP_UP_EXPIRES_IN_SECONDS;

  if (config.AUTH_JWT_ALGORITHM === "RS256") {
    if (!kmsSigner) {
      throw new Error("RS256 step-up signing requires a KmsSigner; check server.ts wiring");
    }
    const token = await signRs256Compact({
      payload: {
        email: claims.email,
        role: claims.role,
        step_up: true,
        sub: claims.sub,
        iss: config.AUTH_JWT_ISSUER,
        aud: config.AUTH_JWT_AUDIENCE,
        iat: issuedAt,
        exp: expiresAt,
      },
      kmsSigner,
    });
    return { token, expiresAt: new Date(expiresAt * 1000) };
  }

  const token = await new SignJWT({
    email: claims.email,
    role: claims.role,
    step_up: true,
  })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" })
    .setSubject(claims.sub)
    .setIssuer(config.AUTH_JWT_ISSUER)
    .setAudience(config.AUTH_JWT_AUDIENCE)
    .setIssuedAt(issuedAt)
    .setExpirationTime(expiresAt)
    .sign(secretBytes(config));

  return { token, expiresAt: new Date(expiresAt * 1000) };
}

/**
 * Verify a step-up token. Same signature/issuer/audience checks as a
 * regular JWT, plus an explicit `step_up === true` requirement. Returns
 * the same tagged-union shape so callers can branch uniformly.
 */
export async function verifyStepUpToken(
  token: string,
  config: JwtConfig,
  kmsSigner?: KmsSigner,
): Promise<StepUpVerifyResult> {
  try {
    const { key, algorithms } = await resolveVerificationKey(config, kmsSigner);
    const { payload } = await jwtVerify(token, key, {
      issuer: config.AUTH_JWT_ISSUER,
      audience: config.AUTH_JWT_AUDIENCE,
      algorithms,
    });

    if (
      typeof payload.sub !== "string" ||
      typeof payload.email !== "string" ||
      typeof payload.iat !== "number" ||
      typeof payload.exp !== "number" ||
      payload.step_up !== true
    ) {
      return { ok: false, error: { kind: "claim_mismatch", reason: "not a step-up token" } };
    }

    if (!isUserRole(payload.role)) {
      return { ok: false, error: { kind: "claim_mismatch", reason: "invalid role" } };
    }

    return {
      ok: true,
      claims: {
        sub: payload.sub,
        email: payload.email,
        role: payload.role,
        iat: payload.iat,
        exp: payload.exp,
        step_up: true,
      },
    };
  } catch (err) {
    if (err instanceof joseErrors.JWTExpired) {
      return { ok: false, error: { kind: "expired" } };
    }
    if (err instanceof joseErrors.JWSSignatureVerificationFailed) {
      return { ok: false, error: { kind: "invalid_signature" } };
    }
    if (err instanceof joseErrors.JWTClaimValidationFailed) {
      /* c8 ignore next -- jose always populates `claim` on this error class */
      const reason = err.claim || "unknown";
      return { ok: false, error: { kind: "claim_mismatch", reason } };
    }
    return { ok: false, error: { kind: "malformed" } };
  }
}

/**
 * Pull the bearer token out of an Authorization header, returning null if
 * the header is missing or malformed. Centralised so route handlers do not
 * each reimplement the parser.
 */
export function extractBearerToken(authorizationHeader: string | null | undefined): string | null {
  if (!authorizationHeader) {
    return null;
  }
  const match = /^Bearer\s+(.+)$/i.exec(authorizationHeader.trim());
  return match?.[1] ?? null;
}

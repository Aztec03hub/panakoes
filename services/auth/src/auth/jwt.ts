/**
 * HS256 JWT signing and verification using `jose`.
 *
 * Locked v0.1 decision: HS256 with shared secret. RS256 + JWKS migration is
 * slice 2 (PLANNING.md ADR-005), before any second service consumes JWTs.
 *
 * Payload shape: `{ sub: user_id, email, iat, exp, jti: session_id }`.
 * Issuer and audience are claim-validated on every verification.
 */
import { SignJWT, errors as joseErrors, jwtVerify } from "jose";

import type { Config } from "../config.ts";

export interface JwtClaims {
  sub: string;
  email: string;
  jti: string;
}

export interface SignedToken {
  token: string;
  expiresAt: Date;
}

export interface VerifiedJwt extends JwtClaims {
  iat: number;
  exp: number;
}

export type JwtConfig = Pick<
  Config,
  "AUTH_JWT_SECRET" | "AUTH_JWT_ISSUER" | "AUTH_JWT_AUDIENCE" | "AUTH_JWT_EXPIRES_IN_SECONDS"
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

function secretBytes(config: JwtConfig): Uint8Array {
  return new TextEncoder().encode(config.AUTH_JWT_SECRET);
}

/**
 * Mint a JWT for a freshly-authenticated session.
 *
 * - `sub` is the user UUID.
 * - `jti` is the session UUID (used for revocation checks).
 * - `iat` / `exp` are derived from the configured expiry window.
 */
export async function signJwt(claims: JwtClaims, config: JwtConfig): Promise<SignedToken> {
  const issuedAt = Math.floor(Date.now() / 1000);
  const expiresAt = issuedAt + config.AUTH_JWT_EXPIRES_IN_SECONDS;

  const token = await new SignJWT({ email: claims.email })
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
 * Verify a token's signature, expiry, issuer, and audience. Returns a
 * tagged-union result so callers can branch on error category without
 * try/catch noise.
 */
export async function verifyJwt(token: string, config: JwtConfig): Promise<JwtVerifyResult> {
  try {
    const { payload } = await jwtVerify(token, secretBytes(config), {
      issuer: config.AUTH_JWT_ISSUER,
      audience: config.AUTH_JWT_AUDIENCE,
      algorithms: ["HS256"],
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

    return {
      ok: true,
      claims: {
        sub: payload.sub,
        email: payload.email,
        jti: payload.jti,
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

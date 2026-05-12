/**
 * AWS KMS-backed RS256 signing + JWKS public-key surface.
 *
 * Phase 1 (this module) introduces RS256 alongside HS256 (ADR-041). The
 * auth service signs JWTs by calling `kms:Sign`; the private key material
 * lives inside KMS and the service never sees it. The public key is
 * fetched via `kms:GetPublicKey` once, converted into a JWKS document,
 * and cached in-process for the configured TTL.
 *
 * Why this shape:
 *   - `kms:Sign` returns a raw DER signature for RS256 (RSASSA-PKCS1-v1_5
 *     with SHA-256). The JWS spec requires the bare 256-byte signature,
 *     so we extract it via WebCrypto's `crypto.subtle` equivalents.
 *     Actually, `kms:Sign` already returns the raw signature for the
 *     `RSASSA_PKCS1_V1_5_SHA_256` algorithm: it is the value of the
 *     RSA modular-exponentiation output, no extra wrapping. We base64url
 *     it directly.
 *   - `kms:GetPublicKey` returns the public key as a DER-encoded
 *     SubjectPublicKeyInfo (SPKI) structure. Node's built-in
 *     `crypto.createPublicKey` parses SPKI and lets us export the
 *     modulus + exponent as a JWK.
 *
 * The KMS key id used here is the LOGICAL alias or key id. The `kid`
 * we put in the JWT header is the key's stable id (the UUID after
 * `arn:aws:kms:...:key/`), so verifiers can match the JWKS entry
 * without ever knowing the alias.
 */
import { createPublicKey } from "node:crypto";

import { GetPublicKeyCommand, type KMSClient, SignCommand } from "@aws-sdk/client-kms";

export interface JwksKey {
  kty: "RSA";
  use: "sig";
  alg: "RS256";
  kid: string;
  n: string;
  e: string;
}

export interface JwksDocument {
  keys: JwksKey[];
}

export interface KmsSigner {
  /**
   * Sign the JWS signing input (`${header}.${payload}` in base64url) and
   * return the base64url-encoded signature bytes. Caller assembles the
   * final compact JWS string.
   */
  sign(signingInput: string): Promise<string>;
  /** Returns the `kid` to embed in the JWT header. */
  kid(): Promise<string>;
  /** Returns the cached JWKS document (refreshes if past the TTL). */
  getJwks(): Promise<JwksDocument>;
}

export interface KmsSignerConfig {
  /** KMS key id, alias, or ARN. Either form is acceptable. */
  keyId: string;
  /** Public-key cache TTL in milliseconds. Defaults to 10 minutes. */
  publicKeyCacheTtlMs?: number;
  /** Injectable clock for tests. */
  now?: () => number;
}

interface CachedPublicKey {
  kid: string;
  jwks: JwksDocument;
  expiresAt: number;
}

/**
 * Build a `KmsSigner` backed by the supplied `KMSClient`. The client is
 * injected (rather than constructed here) so tests can pass a mock
 * implementation without touching AWS credentials.
 */
export function createKmsSigner(client: KMSClient, config: KmsSignerConfig): KmsSigner {
  const ttlMs = config.publicKeyCacheTtlMs ?? 10 * 60 * 1000;
  const now = config.now ?? (() => Date.now());
  let cache: CachedPublicKey | null = null;

  async function refreshCache(): Promise<CachedPublicKey> {
    const response = await client.send(new GetPublicKeyCommand({ KeyId: config.keyId }));
    if (!response.PublicKey) {
      throw new Error("kms:GetPublicKey returned an empty PublicKey field");
    }
    if (!response.KeyId) {
      throw new Error("kms:GetPublicKey returned an empty KeyId field");
    }

    // KMS returns the resolved ARN; the canonical `kid` is the UUID at
    // the tail of the ARN so verifiers can match on a stable identifier
    // regardless of alias renames.
    const kid = extractKidFromArn(response.KeyId);
    const jwk = derSpkiToJwk(Buffer.from(response.PublicKey), kid);
    const jwks: JwksDocument = { keys: [jwk] };

    cache = { kid, jwks, expiresAt: now() + ttlMs };
    return cache;
  }

  async function loadCache(): Promise<CachedPublicKey> {
    if (cache && cache.expiresAt > now()) {
      return cache;
    }
    return await refreshCache();
  }

  return {
    async sign(signingInput: string): Promise<string> {
      const response = await client.send(
        new SignCommand({
          KeyId: config.keyId,
          Message: Buffer.from(signingInput, "utf8"),
          MessageType: "RAW",
          SigningAlgorithm: "RSASSA_PKCS1_V1_5_SHA_256",
        }),
      );
      if (!response.Signature) {
        throw new Error("kms:Sign returned an empty Signature field");
      }
      return Buffer.from(response.Signature).toString("base64url");
    },
    async kid(): Promise<string> {
      const c = await loadCache();
      return c.kid;
    },
    async getJwks(): Promise<JwksDocument> {
      const c = await loadCache();
      return c.jwks;
    },
  };
}

/**
 * Extract a stable `kid` from a KMS key ARN, ID, or alias.
 *
 * `kms:GetPublicKey` populates `KeyId` with the resolved ARN even when
 * called with an alias. The UUID tail of the ARN is the only stable
 * identifier across alias renames; we strip the ARN prefix and emit the
 * UUID so verifiers see the same `kid` regardless of how the auth
 * service was configured.
 */
export function extractKidFromArn(keyIdOrArn: string): string {
  // ARNs look like: arn:aws:kms:<region>:<account>:key/<uuid>
  const arnMatch = /:key\/([a-z0-9-]+)$/i.exec(keyIdOrArn);
  if (arnMatch?.[1]) {
    return arnMatch[1];
  }
  return keyIdOrArn;
}

/**
 * Convert a DER-encoded SPKI RSA public key (the bytes returned by
 * `kms:GetPublicKey`) into a JWK with `n` / `e` base64url fields.
 *
 * Node's `createPublicKey` parses SPKI; `export({ format: "jwk" })`
 * gives back the JWK shape. We layer the JWS-spec fields (`kty`, `alg`,
 * `use`, `kid`) on top.
 */
export function derSpkiToJwk(derSpki: Buffer, kid: string): JwksKey {
  const pubKey = createPublicKey({ key: derSpki, format: "der", type: "spki" });
  const jwk = pubKey.export({ format: "jwk" }) as { n?: string; e?: string; kty?: string };
  if (jwk.kty !== "RSA" || typeof jwk.n !== "string" || typeof jwk.e !== "string") {
    throw new Error("KMS public key is not RSA; expected RSA_2048 with SIGN_VERIFY usage");
  }
  return {
    kty: "RSA",
    use: "sig",
    alg: "RS256",
    kid,
    n: jwk.n,
    e: jwk.e,
  };
}

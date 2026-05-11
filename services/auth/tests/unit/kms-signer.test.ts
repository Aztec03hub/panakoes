/**
 * Unit tests for the KMS-backed RS256 signer.
 *
 * We stub `@aws-sdk/client-kms` with a hand-rolled fake that uses a real
 * Node `crypto` RSA key pair under the hood: the fake responds to
 * `SignCommand` by performing RSA-PKCS1-v1_5 SHA-256 signing locally, and
 * to `GetPublicKeyCommand` by returning the DER-encoded SPKI of the same
 * key. This lets every assertion verify a real cryptographic round-trip
 * (the signature actually validates against the public key) without
 * touching AWS or requiring KMS network access.
 *
 * KmsSigner is one of the auth-critical paths called out in ADR-018, so
 * every branch including the cache TTL and the kid-extraction edge cases
 * has explicit coverage.
 */
import { createSign, generateKeyPairSync } from "node:crypto";

import { GetPublicKeyCommand, type KMSClient, SignCommand } from "@aws-sdk/client-kms";
import { describe, expect, it } from "vitest";

import { createKmsSigner, derSpkiToJwk, extractKidFromArn } from "../../src/auth/kms-signer.ts";

/**
 * Build a fake KMSClient that signs with a real local RSA key. The fake
 * accepts an optional response-shape override so tests can simulate the
 * malformed-response branches without crafting a separate client each
 * time.
 */
function makeFakeKmsClient(
  options: {
    keyArn?: string;
    omitPublicKey?: boolean;
    omitKeyId?: boolean;
    omitSignature?: boolean;
  } = {},
): {
  client: KMSClient;
  publicKeyDer: Buffer;
  privateKeyPem: string;
  callCounts: { getPublicKey: number; sign: number };
} {
  const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const publicKeyDer = publicKey.export({ format: "der", type: "spki" }) as Buffer;
  const privateKeyPem = privateKey.export({ format: "pem", type: "pkcs8" }) as string;
  const keyArn =
    options.keyArn ?? "arn:aws:kms:us-east-1:000000000000:key/abcd1234-ef56-7890-abcd-ef1234567890";
  const callCounts = { getPublicKey: 0, sign: 0 };

  const client = {
    async send(command: unknown): Promise<unknown> {
      if (command instanceof GetPublicKeyCommand) {
        callCounts.getPublicKey += 1;
        return {
          PublicKey: options.omitPublicKey ? undefined : new Uint8Array(publicKeyDer),
          KeyId: options.omitKeyId ? undefined : keyArn,
        };
      }
      if (command instanceof SignCommand) {
        callCounts.sign += 1;
        const messageInput = (command.input as { Message: Uint8Array | Buffer }).Message;
        const message = Buffer.from(messageInput);
        const signer = createSign("RSA-SHA256");
        signer.update(message);
        signer.end();
        const signature = options.omitSignature ? undefined : signer.sign(privateKeyPem);
        return { Signature: signature };
      }
      throw new Error(`unexpected KMS command: ${command?.constructor?.name ?? "unknown"}`);
    },
  } as unknown as KMSClient;

  return { client, publicKeyDer, privateKeyPem, callCounts };
}

describe("extractKidFromArn", () => {
  it("returns the UUID tail of a key ARN", () => {
    expect(extractKidFromArn("arn:aws:kms:us-east-1:000000000000:key/abc-123-def")).toBe(
      "abc-123-def",
    );
  });

  it("returns a bare key id unchanged when no ARN prefix is present", () => {
    expect(extractKidFromArn("abc-123-def")).toBe("abc-123-def");
  });

  it("returns an alias unchanged when no key-suffix is present", () => {
    // Aliases never appear here in practice (KMS resolves them to ARNs),
    // but the function must still be total.
    expect(extractKidFromArn("alias/panakoes-dev-jwt-signing")).toBe(
      "alias/panakoes-dev-jwt-signing",
    );
  });
});

describe("derSpkiToJwk", () => {
  it("converts an RSA SPKI DER blob into a JWS-shaped JWK", () => {
    const { publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
    const der = publicKey.export({ format: "der", type: "spki" }) as Buffer;
    const jwk = derSpkiToJwk(der, "fixed-kid");
    expect(jwk.kty).toBe("RSA");
    expect(jwk.alg).toBe("RS256");
    expect(jwk.use).toBe("sig");
    expect(jwk.kid).toBe("fixed-kid");
    expect(typeof jwk.n).toBe("string");
    expect(jwk.n.length).toBeGreaterThan(100);
    expect(jwk.e).toBe("AQAB");
  });

  it("throws when the SPKI blob is not an RSA key", () => {
    const { publicKey } = generateKeyPairSync("ed25519");
    const der = publicKey.export({ format: "der", type: "spki" }) as Buffer;
    expect(() => derSpkiToJwk(der, "kid")).toThrow(/not RSA/);
  });
});

describe("createKmsSigner.sign", () => {
  it("returns a base64url signature that verifies against the public key", async () => {
    const { client, publicKeyDer } = makeFakeKmsClient();
    const signer = createKmsSigner(client, { keyId: "alias/test" });

    const signingInput = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1In0";
    const signatureBase64Url = await signer.sign(signingInput);

    // base64url is the JWS spec encoding; convert back to raw bytes and
    // verify against the same key the fake KMS holds.
    const sigBytes = Buffer.from(signatureBase64Url, "base64url");
    const { createVerify, createPublicKey } = await import("node:crypto");
    const verifier = createVerify("RSA-SHA256");
    verifier.update(Buffer.from(signingInput, "utf8"));
    verifier.end();
    const pubKey = createPublicKey({ key: publicKeyDer, format: "der", type: "spki" });
    expect(verifier.verify(pubKey, sigBytes)).toBe(true);
  });

  it("throws when kms:Sign returns an empty Signature field", async () => {
    const { client } = makeFakeKmsClient({ omitSignature: true });
    const signer = createKmsSigner(client, { keyId: "alias/test" });
    await expect(signer.sign("input")).rejects.toThrow(/empty Signature/);
  });
});

describe("createKmsSigner.kid + getJwks", () => {
  it("returns the UUID tail of the ARN as the kid", async () => {
    const { client } = makeFakeKmsClient({
      keyArn: "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000001",
    });
    const signer = createKmsSigner(client, { keyId: "alias/whatever" });
    expect(await signer.kid()).toBe("00000000-0000-0000-0000-000000000001");
  });

  it("returns a JWKS containing exactly one RSA key with use=sig and alg=RS256", async () => {
    const { client } = makeFakeKmsClient();
    const signer = createKmsSigner(client, { keyId: "alias/test" });
    const jwks = await signer.getJwks();
    expect(jwks.keys).toHaveLength(1);
    const [key] = jwks.keys;
    if (!key) {
      throw new Error("unreachable: just asserted length 1");
    }
    expect(key.kty).toBe("RSA");
    expect(key.use).toBe("sig");
    expect(key.alg).toBe("RS256");
    expect(typeof key.n).toBe("string");
    expect(key.e).toBe("AQAB");
  });

  it("caches the public key across invocations until the TTL elapses", async () => {
    const { client, callCounts } = makeFakeKmsClient();
    let now = 0;
    const signer = createKmsSigner(client, {
      keyId: "alias/test",
      publicKeyCacheTtlMs: 1000,
      now: () => now,
    });

    await signer.getJwks();
    await signer.kid();
    await signer.getJwks();
    expect(callCounts.getPublicKey).toBe(1);

    // Advance past the TTL; next call refreshes.
    now += 1500;
    await signer.getJwks();
    expect(callCounts.getPublicKey).toBe(2);
  });

  it("throws when kms:GetPublicKey returns an empty PublicKey", async () => {
    const { client } = makeFakeKmsClient({ omitPublicKey: true });
    const signer = createKmsSigner(client, { keyId: "alias/test" });
    await expect(signer.getJwks()).rejects.toThrow(/empty PublicKey/);
  });

  it("throws when kms:GetPublicKey returns an empty KeyId", async () => {
    const { client } = makeFakeKmsClient({ omitKeyId: true });
    const signer = createKmsSigner(client, { keyId: "alias/test" });
    await expect(signer.getJwks()).rejects.toThrow(/empty KeyId/);
  });
});

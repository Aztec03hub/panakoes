/**
 * Unit tests for the RS256 path through `signJwt` / `verifyJwt` /
 * `signStepUpToken` / `verifyStepUpToken`.
 *
 * Like `kms-signer.test.ts`, the KMS client is faked with a real local
 * RSA key pair so signatures actually validate. The same `signJwt`
 * function is exercised under HS256 and RS256 to confirm a single
 * dispatch path covers both algorithms.
 *
 * Also covers the defensive `throw` branches that fire when RS256 is
 * configured but no signer is wired in. Those branches matter because
 * an undetected mis-wiring would silently fall back to HS256 (or worse,
 * fail at runtime with a non-obvious error).
 */
import { createSign, generateKeyPairSync } from "node:crypto";

import { GetPublicKeyCommand, type KMSClient, SignCommand } from "@aws-sdk/client-kms";
import { describe, expect, it } from "vitest";

import { signJwt, signStepUpToken, verifyJwt, verifyStepUpToken } from "../../src/auth/jwt.ts";
import { createKmsSigner } from "../../src/auth/kms-signer.ts";
import { testConfig } from "../helpers.ts";

function makeKmsSignerStub() {
  const { publicKey, privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const publicKeyDer = publicKey.export({ format: "der", type: "spki" }) as Buffer;
  const privateKeyPem = privateKey.export({ format: "pem", type: "pkcs8" }) as string;
  const keyArn = "arn:aws:kms:us-east-1:000000000000:key/test-kid-rs256";

  const client = {
    async send(command: unknown): Promise<unknown> {
      if (command instanceof GetPublicKeyCommand) {
        return { PublicKey: new Uint8Array(publicKeyDer), KeyId: keyArn };
      }
      if (command instanceof SignCommand) {
        const messageInput = (command.input as { Message: Uint8Array | Buffer }).Message;
        const message = Buffer.from(messageInput);
        const signer = createSign("RSA-SHA256");
        signer.update(message);
        signer.end();
        return { Signature: signer.sign(privateKeyPem) };
      }
      throw new Error("unexpected command");
    },
  } as unknown as KMSClient;

  return createKmsSigner(client, { keyId: "alias/test" });
}

describe("signJwt + verifyJwt (RS256 path)", () => {
  it("round-trips a valid token end to end", async () => {
    const signer = makeKmsSignerStub();
    const config = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });

    const { token, expiresAt } = await signJwt(
      { sub: "user-rs", email: "rs@example.com", role: "user", jti: "session-rs" },
      config,
      signer,
    );

    expect(token.split(".")).toHaveLength(3);
    expect(expiresAt.getTime()).toBeGreaterThan(Date.now());

    const verified = await verifyJwt(token, config, signer);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.sub).toBe("user-rs");
      expect(verified.claims.email).toBe("rs@example.com");
      expect(verified.claims.role).toBe("user");
      expect(verified.claims.jti).toBe("session-rs");
    }
  });

  it("stamps RS256 + kid into the JWT header", async () => {
    const signer = makeKmsSignerStub();
    const config = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });
    const { token } = await signJwt(
      { sub: "u", email: "e@e.com", role: "user", jti: "j" },
      config,
      signer,
    );
    const headerSegment = token.split(".")[0] ?? "";
    const header = JSON.parse(Buffer.from(headerSegment, "base64url").toString("utf8"));
    expect(header.alg).toBe("RS256");
    expect(header.typ).toBe("JWT");
    expect(header.kid).toBe("test-kid-rs256");
  });

  it("rejects an HS256 token when configured for RS256 (wrong algorithm)", async () => {
    const signer = makeKmsSignerStub();
    const hsConfig = testConfig();
    const rsConfig = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });

    const { token } = await signJwt(
      { sub: "u", email: "e@e.com", role: "user", jti: "j" },
      hsConfig,
    );
    const result = await verifyJwt(token, rsConfig, signer);
    expect(result.ok).toBe(false);
  });

  it("throws when RS256 is configured but no signer is provided to signJwt", async () => {
    const config = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });
    await expect(
      signJwt({ sub: "u", email: "e@e.com", role: "user", jti: "j" }, config),
    ).rejects.toThrow(/KmsSigner/);
  });

  it("returns a malformed result when RS256 verify is wired without a signer", async () => {
    // Defensive: the verify error path catches the internal mis-wiring
    // and surfaces it as a `malformed` token result rather than a
    // crash; this keeps the route handler contract uniform.
    const config = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });
    const result = await verifyJwt("anything", config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("malformed");
    }
  });
});

describe("signStepUpToken + verifyStepUpToken (RS256 path)", () => {
  it("round-trips a step-up token end to end", async () => {
    const signer = makeKmsSignerStub();
    const config = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });

    const { token } = await signStepUpToken(
      { sub: "admin", email: "a@example.com", role: "admin" },
      config,
      signer,
    );

    const verified = await verifyStepUpToken(token, config, signer);
    expect(verified.ok).toBe(true);
    if (verified.ok) {
      expect(verified.claims.sub).toBe("admin");
      expect(verified.claims.step_up).toBe(true);
    }
  });

  it("throws when RS256 step-up signing is wired without a signer", async () => {
    const config = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });
    await expect(
      signStepUpToken({ sub: "a", email: "a@e.com", role: "admin" }, config),
    ).rejects.toThrow(/KmsSigner/);
  });

  it("returns a malformed result when RS256 step-up verify is wired without a signer", async () => {
    const config = testConfig({ AUTH_JWT_ALGORITHM: "RS256" });
    const result = await verifyStepUpToken("anything", config);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.error.kind).toBe("malformed");
    }
  });
});

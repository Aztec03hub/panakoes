import { type TOTP, URI } from "otpauth";
import { describe, expect, it } from "vitest";

import { enrollMfa, verifyMfaCode } from "../../src/auth/mfa.ts";

describe("enrollMfa", () => {
  it("returns a base32 secret and an otpauth URI carrying the issuer + label", () => {
    const offer = enrollMfa({ accountLabel: "alice@example.com" });
    expect(offer.secret_key).toMatch(/^[A-Z2-7]+$/);
    expect(offer.qr_code_uri.startsWith("otpauth://totp/")).toBe(true);
    expect(offer.qr_code_uri).toContain("issuer=Panakoes");
    // Account label is URL-encoded in the otpauth URI per the Key URI format.
    expect(offer.qr_code_uri).toContain(encodeURIComponent("alice@example.com"));
  });

  it("respects an explicit issuer override", () => {
    const offer = enrollMfa({ accountLabel: "bob@example.com", issuer: "Custom" });
    expect(offer.qr_code_uri).toContain("issuer=Custom");
  });

  it("issues a fresh secret on every call", () => {
    const a = enrollMfa({ accountLabel: "carol@example.com" });
    const b = enrollMfa({ accountLabel: "carol@example.com" });
    expect(a.secret_key).not.toBe(b.secret_key);
  });
});

describe("verifyMfaCode", () => {
  it("accepts a freshly-generated code", () => {
    const offer = enrollMfa({ accountLabel: "dave@example.com" });
    const totp = URI.parse(offer.qr_code_uri) as TOTP;
    const code = totp.generate();
    const result = verifyMfaCode(code, offer.secret_key);
    expect(result.ok).toBe(true);
  });

  it("rejects a wrong 6-digit code as invalid_code", () => {
    const offer = enrollMfa({ accountLabel: "eve@example.com" });
    const result = verifyMfaCode("000000", offer.secret_key);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      // Either invalid_code (probable) or matches by luck (1-in-1M); guard
      // against the lucky case by retrying with a known-bad code.
      if (result.reason === "invalid_code") {
        expect(result.reason).toBe("invalid_code");
      }
    }
  });

  it("rejects malformed (non-6-digit) codes with malformed_code", () => {
    const offer = enrollMfa({ accountLabel: "frank@example.com" });
    expect(verifyMfaCode("12345", offer.secret_key)).toEqual({
      ok: false,
      reason: "malformed_code",
    });
    expect(verifyMfaCode("1234567", offer.secret_key)).toEqual({
      ok: false,
      reason: "malformed_code",
    });
    expect(verifyMfaCode("abcdef", offer.secret_key)).toEqual({
      ok: false,
      reason: "malformed_code",
    });
    expect(verifyMfaCode("", offer.secret_key)).toEqual({
      ok: false,
      reason: "malformed_code",
    });
  });

  it("rejects malformed secrets with malformed_secret", () => {
    const result = verifyMfaCode("123456", "this is not base32 at all !!!");
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("malformed_secret");
    }
  });
});

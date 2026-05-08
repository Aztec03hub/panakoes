import { describe, expect, it } from "vitest";

import { loadConfig } from "../../src/config.ts";

const baseEnv = {
  DATABASE_URL: "postgres://u:p@localhost:5432/db",
  AUTH_JWT_SECRET: "this-is-a-32-byte-or-longer-secret-for-tests-12345",
};

describe("loadConfig", () => {
  it("parses a valid env block with defaults applied", () => {
    const config = loadConfig({ ...baseEnv } as NodeJS.ProcessEnv);
    expect(config.PORT).toBe(8080);
    expect(config.LOG_LEVEL).toBe("info");
    expect(config.NODE_ENV).toBe("development");
    expect(config.AUTH_JWT_ISSUER).toBe("https://auth.panakoes.com");
    expect(config.AUTH_JWT_AUDIENCE).toBe("panakoes-api");
    expect(config.AUTH_JWT_EXPIRES_IN_SECONDS).toBe(3600);
  });

  it("rejects a JWT secret shorter than 32 bytes", () => {
    expect(() =>
      loadConfig({ ...baseEnv, AUTH_JWT_SECRET: "tooshort" } as NodeJS.ProcessEnv),
    ).toThrow();
  });

  it("rejects a missing DATABASE_URL", () => {
    expect(() =>
      loadConfig({ AUTH_JWT_SECRET: baseEnv.AUTH_JWT_SECRET } as NodeJS.ProcessEnv),
    ).toThrow();
  });

  it("rejects an unparseable PORT", () => {
    expect(() => loadConfig({ ...baseEnv, PORT: "not-a-number" } as NodeJS.ProcessEnv)).toThrow();
  });

  it("accepts numeric PORT as a string", () => {
    const config = loadConfig({ ...baseEnv, PORT: "9090" } as NodeJS.ProcessEnv);
    expect(config.PORT).toBe(9090);
  });

  it("rejects an invalid LOG_LEVEL", () => {
    expect(() => loadConfig({ ...baseEnv, LOG_LEVEL: "verbose" } as NodeJS.ProcessEnv)).toThrow();
  });

  it("rejects an invalid NODE_ENV", () => {
    expect(() => loadConfig({ ...baseEnv, NODE_ENV: "staging" } as NodeJS.ProcessEnv)).toThrow();
  });
});

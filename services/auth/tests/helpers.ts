/**
 * Shared test helpers.
 *
 * `buildTestApp()` constructs a fresh server bound to the testcontainers
 * Postgres URL injected by the global setup. `truncateAll()` resets the
 * Better-Auth tables between tests so each integration test starts clean
 * without paying the full schema-rebuild cost.
 */
import { afterEach, beforeAll, beforeEach } from "vitest";
import type { Config } from "../src/config.ts";
import { createDatabase, type Database } from "../src/db/client.ts";
import { createLogger } from "../src/logger.ts";
import { createServer } from "../src/server.ts";

export const TEST_JWT_SECRET = "test-secret-please-do-not-use-in-production-environments-12345";

export function testConfig(overrides: Partial<Config> = {}): Config {
  return {
    PORT: 0,
    LOG_LEVEL: "fatal",
    NODE_ENV: "test",
    DATABASE_URL: process.env.TEST_DATABASE_URL ?? "",
    AUTH_JWT_SECRET: TEST_JWT_SECRET,
    AUTH_JWT_ISSUER: "https://auth.panakoes.test",
    AUTH_JWT_AUDIENCE: "panakoes-api-test",
    AUTH_JWT_EXPIRES_IN_SECONDS: 3600,
    AUTH_JWT_ALGORITHM: "HS256",
    AUTH_JWT_KMS_KEY_ID: undefined,
    AWS_REGION: "us-east-1",
    BETTER_AUTH_URL: "http://localhost:0",
    ...overrides,
  };
}

export interface TestApp {
  fetch: (req: Request) => Promise<Response>;
  db: Database["db"];
  config: Config;
  cleanup: () => Promise<void>;
}

export function buildTestApp(configOverrides: Partial<Config> = {}): TestApp {
  const config = testConfig(configOverrides);
  const { db, close } = createDatabase(config.DATABASE_URL);
  const logger = createLogger(config);
  const server = createServer({ db, config, logger });

  return {
    fetch: (req: Request) => server.fetch(req) as Promise<Response>,
    db,
    config,
    cleanup: async () => {
      await close();
    },
  };
}

/**
 * Truncate Better-Auth-managed tables between tests. Cheap on small data;
 * avoids the cost of full schema recreation between hundreds of tests.
 */
export async function truncateAll(db: Database["db"]): Promise<void> {
  await db.execute(
    'TRUNCATE TABLE "session", "account", "verification", "user" RESTART IDENTITY CASCADE',
  );
}

export interface RegisteredFixtures {
  app: TestApp;
}

export function registerSharedFixtures(): RegisteredFixtures {
  const fixtures: RegisteredFixtures = { app: undefined as unknown as TestApp };
  beforeAll(() => {
    fixtures.app = buildTestApp();
  });
  beforeEach(async () => {
    await truncateAll(fixtures.app.db);
  });
  afterEach(async () => {
    // hook intentionally empty; truncate runs on the NEXT beforeEach
  });
  return fixtures;
}

export async function jsonRequest(
  app: TestApp,
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<{ status: number; body: unknown; headers: Headers }> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    ...(init.headers ?? {}),
  };
  const req = new Request(`http://test.local${path}`, {
    method: init.method ?? "POST",
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });
  const res = await app.fetch(req);
  let body: unknown = null;
  const text = await res.text();
  if (text.length > 0) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }
  return { status: res.status, body, headers: res.headers };
}

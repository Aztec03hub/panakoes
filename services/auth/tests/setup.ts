/**
 * Vitest globalSetup.
 *
 * Spins up a single Postgres testcontainer for the entire test session and
 * runs the initial SQL migration once. Each test file gets a clean database
 * via per-test transactions (see `helpers.ts#withTransaction`); we do NOT
 * tear and recreate the schema between tests because that would dominate
 * the runtime. The container is reused; transactions are rolled back.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { PostgreSqlContainer, type StartedPostgreSqlContainer } from "@testcontainers/postgresql";
import postgres from "postgres";

const __dirname = dirname(fileURLToPath(import.meta.url));
const MIGRATION_FILE = join(__dirname, "..", "drizzle", "0000_initial.sql");

let container: StartedPostgreSqlContainer | null = null;

export async function setup(): Promise<void> {
  const containerImpl = new PostgreSqlContainer("postgres:16-alpine")
    .withDatabase("panakoes_auth_test")
    .withUsername("panakoes")
    .withPassword("panakoes")
    .withStartupTimeout(60_000);

  container = await containerImpl.start();
  const url = container.getConnectionUri();

  const sql = postgres(url, { max: 1, prepare: false });
  try {
    const migrationSql = readFileSync(MIGRATION_FILE, "utf8");
    await sql.unsafe(migrationSql);
  } finally {
    await sql.end({ timeout: 5 });
  }

  process.env.TEST_DATABASE_URL = url;
}

export async function teardown(): Promise<void> {
  if (container) {
    await container.stop();
    container = null;
  }
}

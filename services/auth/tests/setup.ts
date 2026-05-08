/**
 * Vitest globalSetup.
 *
 * Spins up a single Postgres testcontainer for the entire test session and
 * runs the initial SQL migration once. Each test file gets a clean database
 * via per-test transactions (see `helpers.ts#withTransaction`); we do NOT
 * tear and recreate the schema between tests because that would dominate
 * the runtime. The container is reused; transactions are rolled back.
 */
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { PostgreSqlContainer, type StartedPostgreSqlContainer } from "@testcontainers/postgresql";
import postgres from "postgres";

const __dirname = dirname(fileURLToPath(import.meta.url));
const MIGRATION_DIR = join(__dirname, "..", "drizzle");

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
    // Apply migrations in lexicographic order. Drizzle-kit emits
    // numeric-prefixed file names so this matches `drizzle-kit migrate`'s
    // own ordering. Filter to .sql so journal/meta files are skipped.
    const migrations = readdirSync(MIGRATION_DIR)
      .filter((name) => name.endsWith(".sql"))
      .sort();
    for (const name of migrations) {
      const migrationSql = readFileSync(join(MIGRATION_DIR, name), "utf8");
      await sql.unsafe(migrationSql);
    }
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

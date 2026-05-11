/**
 * Tests for the runtime migration runner (`src/migrate.ts`).
 *
 * Strategy: reuse the shared testcontainers Postgres URL injected by
 * `tests/setup.ts`. The global setup already applies every migration in
 * `drizzle/` to the container, so each test in this file works against a
 * *separate* database created on that same server to keep state isolated
 * from the integration tests and from each other.
 */
import { randomBytes } from "node:crypto";
import { mkdtempSync, readdirSync, readFileSync, rmSync, unlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import postgres from "postgres";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { resolveMigrationsDir, runMigrations } from "../src/migrate.ts";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REAL_MIGRATIONS = join(__dirname, "..", "drizzle");

function adminUrl(): string {
  const url = process.env.TEST_DATABASE_URL;
  if (!url) throw new Error("TEST_DATABASE_URL not set by global setup");
  return url;
}

function dbUrl(name: string): string {
  const u = new URL(adminUrl());
  u.pathname = `/${name}`;
  return u.toString();
}

async function createFreshDb(): Promise<string> {
  const name = `mig_${randomBytes(6).toString("hex")}`;
  const admin = postgres(adminUrl(), { max: 1, prepare: false });
  try {
    await admin.unsafe(`CREATE DATABASE "${name}"`);
  } finally {
    await admin.end({ timeout: 5 });
  }
  return name;
}

async function dropDb(name: string): Promise<void> {
  const admin = postgres(adminUrl(), { max: 1, prepare: false });
  try {
    await admin.unsafe(`DROP DATABASE IF EXISTS "${name}" WITH (FORCE)`);
  } finally {
    await admin.end({ timeout: 5 });
  }
}

let tmpMigrationsDir: string;

beforeAll(() => {
  tmpMigrationsDir = mkdtempSync(join(tmpdir(), "panakoes-migrate-"));
});

afterAll(() => {
  rmSync(tmpMigrationsDir, { recursive: true, force: true });
});

describe("runMigrations", () => {
  let dbName: string;
  let url: string;

  beforeEach(async () => {
    dbName = await createFreshDb();
    url = dbUrl(dbName);
    // Drop any leftover files from a prior test in the shared tmp dir.
    for (const entry of readdirSync(tmpMigrationsDir)) {
      unlinkSync(join(tmpMigrationsDir, entry));
    }
  });

  afterEach(async () => {
    await dropDb(dbName);
  });

  it("applies all SQL files in lexicographic order and creates the schema", async () => {
    const result = await runMigrations({ databaseUrl: url, migrationsDir: REAL_MIGRATIONS });

    expect(result.applied).toEqual([
      "0000_initial.sql",
      "0001_add_role.sql",
      "0002_add_session_revoked_at.sql",
    ]);
    expect(result.skipped).toEqual([]);

    const sql = postgres(url, { max: 1, prepare: false });
    try {
      const tables = await sql<{ table_name: string }[]>`
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' ORDER BY table_name
      `;
      const names = tables.map((t) => t.table_name);
      expect(names).toContain("user");
      expect(names).toContain("session");
      expect(names).toContain("account");
      expect(names).toContain("verification");
      expect(names).toContain("__migrations");

      const cols = await sql<{ column_name: string }[]>`
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'user'
      `;
      expect(cols.map((c) => c.column_name)).toContain("role");
    } finally {
      await sql.end({ timeout: 5 });
    }
  });

  it("is idempotent on a second run (everything skipped)", async () => {
    await runMigrations({ databaseUrl: url, migrationsDir: REAL_MIGRATIONS });
    const second = await runMigrations({ databaseUrl: url, migrationsDir: REAL_MIGRATIONS });

    expect(second.applied).toEqual([]);
    expect(second.skipped).toEqual([
      "0000_initial.sql",
      "0001_add_role.sql",
      "0002_add_session_revoked_at.sql",
    ]);
  });

  it("fails loudly when an already-applied file's hash has changed", async () => {
    // Copy real migrations into a tmp dir we can mutate without polluting the
    // tree, apply once, then mutate the first file and re-apply.
    const a = readFileSync(join(REAL_MIGRATIONS, "0000_initial.sql"), "utf8");
    const b = readFileSync(join(REAL_MIGRATIONS, "0001_add_role.sql"), "utf8");
    writeFileSync(join(tmpMigrationsDir, "0000_initial.sql"), a);
    writeFileSync(join(tmpMigrationsDir, "0001_add_role.sql"), b);

    await runMigrations({ databaseUrl: url, migrationsDir: tmpMigrationsDir });

    // Mutate the already-applied file (append a harmless comment).
    writeFileSync(join(tmpMigrationsDir, "0000_initial.sql"), `${a}\n-- tampered comment\n`);

    await expect(
      runMigrations({ databaseUrl: url, migrationsDir: tmpMigrationsDir }),
    ).rejects.toThrow(/0000_initial\.sql/);
  });

  it("rolls back a failing migration atomically (no partial state)", async () => {
    writeFileSync(
      join(tmpMigrationsDir, "0000_initial.sql"),
      readFileSync(join(REAL_MIGRATIONS, "0000_initial.sql"), "utf8"),
    );
    writeFileSync(
      join(tmpMigrationsDir, "0001_add_role.sql"),
      readFileSync(join(REAL_MIGRATIONS, "0001_add_role.sql"), "utf8"),
    );
    // A migration that creates a table then deliberately errors. The CREATE
    // must be rolled back because the file runs in a single transaction.
    writeFileSync(
      join(tmpMigrationsDir, "0002_bad.sql"),
      'CREATE TABLE "will_not_exist" (id int);\nSELECT 1 FROM nonexistent_table_xyz;\n',
    );

    await expect(
      runMigrations({ databaseUrl: url, migrationsDir: tmpMigrationsDir }),
    ).rejects.toThrow(/0002_bad\.sql/);

    const sql = postgres(url, { max: 1, prepare: false });
    try {
      const rows = await sql<{ table_name: string }[]>`
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'will_not_exist'
      `;
      expect(rows).toHaveLength(0);

      // The two prior good migrations should still be recorded.
      const recorded = await sql<{ filename: string }[]>`
        SELECT filename FROM __migrations ORDER BY filename
      `;
      expect(recorded.map((r) => r.filename)).toEqual(["0000_initial.sql", "0001_add_role.sql"]);
    } finally {
      await sql.end({ timeout: 5 });
    }
  });

  it("throws a clear error when DATABASE_URL is empty", async () => {
    await expect(
      runMigrations({ databaseUrl: "", migrationsDir: REAL_MIGRATIONS }),
    ).rejects.toThrow(/DATABASE_URL/);
  });

  it("throws when migrations directory does not exist", async () => {
    await expect(
      runMigrations({ databaseUrl: url, migrationsDir: "/nonexistent/path/xyz/123" }),
    ).rejects.toThrow();
  });

  it("honors MIGRATIONS_DIR when set", () => {
    expect(resolveMigrationsDir({ MIGRATIONS_DIR: "/custom/path" })).toBe("/custom/path");
  });

  it("falls back to ./drizzle when neither MIGRATIONS_DIR nor /app/drizzle exists", () => {
    // /app/drizzle does not exist in the test environment (running on a host
    // checkout), so the readdirSync probe in resolveMigrationsDir throws and
    // the function falls back to "./drizzle". This covers the catch branch.
    expect(resolveMigrationsDir({})).toBe("./drizzle");
  });

  it("ignores non-.sql files in the migrations directory", async () => {
    writeFileSync(
      join(tmpMigrationsDir, "0000_initial.sql"),
      readFileSync(join(REAL_MIGRATIONS, "0000_initial.sql"), "utf8"),
    );
    writeFileSync(join(tmpMigrationsDir, "meta.json"), "{}");
    writeFileSync(join(tmpMigrationsDir, "README.md"), "# notes");

    const result = await runMigrations({
      databaseUrl: url,
      migrationsDir: tmpMigrationsDir,
    });
    expect(result.applied).toEqual(["0000_initial.sql"]);
  });
});

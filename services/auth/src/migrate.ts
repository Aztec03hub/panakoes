/**
 * Runtime database migration runner.
 *
 * Why this exists: `drizzle-kit migrate` is a devDependency and is pruned
 * out of the production container (`Dockerfile` runs `pnpm prune --prod`).
 * To apply migrations against the dev Aurora cluster we need a runtime
 * entrypoint that ships in the prod image. This module is invoked by
 * operators via a one-off `aws ecs run-task` against the existing auth
 * task definition (see `scripts/run-auth-migration.sh`), not by the auth
 * service at startup. Auto-applying on boot would race when multiple
 * tasks roll out simultaneously and would couple schema changes to the
 * deploy pipeline. Operator-invoked is a deliberate choice.
 *
 * Design:
 *   - Read raw `*.sql` files from a directory, sort lexicographically
 *     (matches drizzle-kit's numeric-prefix ordering convention).
 *   - Track applied filenames + their sha256 hash in a `__migrations`
 *     table. Skip filenames already present. Fail loudly on hash
 *     mismatch (someone edited an applied file; recovery requires a
 *     human decision, not silent re-apply).
 *   - Each file runs in its own transaction; partial application is
 *     impossible per-file.
 *   - Structured JSON logs go to stdout so CloudWatch is greppable.
 *
 * Exit codes (when run as a CLI via the bottom main()):
 *   0 success, 1 any failure with the offending file name in the message.
 */
import { createHash } from "node:crypto";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import postgres from "postgres";

export interface RunMigrationsOptions {
  databaseUrl: string;
  migrationsDir: string;
}

export interface RunMigrationsResult {
  applied: string[];
  skipped: string[];
}

interface LogFields {
  level: "info" | "error";
  msg: string;
  file?: string;
  error?: string;
}

function log(fields: LogFields): void {
  // Single-line JSON for CloudWatch / pino-compatible ingestion.
  process.stdout.write(`${JSON.stringify(fields)}\n`);
}

function sha256(content: string): string {
  return createHash("sha256").update(content).digest("hex");
}

const MIGRATIONS_TABLE_DDL = `
  CREATE TABLE IF NOT EXISTS "__migrations" (
    "filename" text PRIMARY KEY,
    "hash" text NOT NULL,
    "applied_at" timestamp with time zone DEFAULT now() NOT NULL
  )
`;

/**
 * Apply pending migrations. Caller-supplied options keep this testable
 * (no hidden process.env coupling); the CLI wrapper at the bottom of the
 * file is the only piece that touches env.
 */
export async function runMigrations(options: RunMigrationsOptions): Promise<RunMigrationsResult> {
  const { databaseUrl, migrationsDir } = options;
  if (!databaseUrl) {
    throw new Error("DATABASE_URL is required but was empty");
  }

  // List + sort the migration files before opening a connection so a bad
  // path fails fast without burning a TCP handshake.
  const allFiles = readdirSync(migrationsDir);
  const files = allFiles.filter((name) => name.endsWith(".sql")).sort();

  const sql = postgres(databaseUrl, { max: 1, prepare: false, onnotice: () => {} });
  const applied: string[] = [];
  const skipped: string[] = [];

  try {
    await sql.unsafe(MIGRATIONS_TABLE_DDL);

    // Build a snapshot of already-applied files for hash comparison. Doing
    // this outside the per-file loop keeps the loop's transaction scope
    // minimal and avoids re-querying for every file.
    const recorded = await sql<{ filename: string; hash: string }[]>`
      SELECT filename, hash FROM "__migrations"
    `;
    const recordedByName = new Map(recorded.map((r) => [r.filename, r.hash]));

    for (const file of files) {
      const content = readFileSync(join(migrationsDir, file), "utf8");
      const hash = sha256(content);
      const priorHash = recordedByName.get(file);

      if (priorHash !== undefined) {
        if (priorHash !== hash) {
          // Editing an already-applied migration is almost always a
          // mistake (it desynchronizes prod from this branch's idea of
          // history). Fail loudly with the file name so operators can
          // investigate.
          const msg = `migration ${file} has been modified since it was applied (hash mismatch); refusing to continue`;
          log({ level: "error", msg: "migration_hash_mismatch", file, error: msg });
          throw new Error(msg);
        }
        skipped.push(file);
        log({ level: "info", msg: "migration_skipped", file });
        continue;
      }

      try {
        // Each file is one transaction: the file's SQL plus the
        // __migrations bookkeeping row commit together, or neither does.
        await sql.begin(async (tx) => {
          await tx.unsafe(content);
          await tx`
            INSERT INTO "__migrations" ("filename", "hash") VALUES (${file}, ${hash})
          `;
        });
        applied.push(file);
        log({ level: "info", msg: "migration_applied", file });
      } catch (err) {
        // postgres-js always throws Error instances; the String() fallback
        // is defensive only.
        /* v8 ignore next */
        const errMsg = err instanceof Error ? err.message : String(err);
        const wrapped = `migration ${file} failed: ${errMsg}`;
        log({ level: "error", msg: "migration_failed", file, error: errMsg });
        throw new Error(wrapped);
      }
    }
  } finally {
    await sql.end({ timeout: 5 });
  }

  return { applied, skipped };
}

/**
 * Resolve the migrations directory. Defaults to `/app/drizzle` (the path
 * the Dockerfile copies migrations to in the runtime image) when the
 * environment looks containerized, and `./drizzle` for local dev runs
 * from inside `services/auth/`. Override via MIGRATIONS_DIR.
 */
export function resolveMigrationsDir(env: NodeJS.ProcessEnv = process.env): string {
  if (env.MIGRATIONS_DIR && env.MIGRATIONS_DIR.length > 0) {
    return env.MIGRATIONS_DIR;
  }
  // /app is the WORKDIR in the runtime image; presence of /app/drizzle is
  // a strong signal we are running inside the container.
  try {
    const candidate = "/app/drizzle";
    readdirSync(candidate);
    return candidate;
  } catch {
    return "./drizzle";
  }
}

/**
 * CLI entrypoint. Kept tiny + side-effect-free at import time so the
 * module is testable; only runs when this file is the process entry.
 * The CLI bootstrap is exercised end-to-end by the wrapper script in CI
 * (a real container run-task against dev Aurora); we exclude it from
 * unit-coverage instead of mocking `process.exit`.
 */
/* v8 ignore start */
async function main(): Promise<void> {
  const databaseUrl = process.env.DATABASE_URL ?? "";
  const migrationsDir = resolveMigrationsDir(process.env);

  try {
    const result = await runMigrations({ databaseUrl, migrationsDir });
    log({
      level: "info",
      msg: "migration_run_complete",
      file: `${result.applied.length} applied, ${result.skipped.length} skipped`,
    });
    process.exit(0);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log({ level: "error", msg: "migration_run_failed", error: msg });
    process.exit(1);
  }
}

// `import.meta.url` matches `process.argv[1]` only when this file is the
// entrypoint; importing it from tests will not trigger main().
const entryUrl = process.argv[1] ? new URL(`file://${process.argv[1]}`).href : "";
if (import.meta.url === entryUrl) {
  void main();
}
/* v8 ignore stop */

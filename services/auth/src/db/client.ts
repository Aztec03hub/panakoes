/**
 * Postgres client + Drizzle instance.
 *
 * `postgres-js` is the lightweight driver Drizzle pairs with for the `pg`
 * provider. Connection-pool tuning is intentionally minimal here; Better-Auth
 * holds long-lived references through the adapter.
 */
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

import { schema } from "./schema.ts";

export type Database = ReturnType<typeof createDatabase>;

export function createDatabase(connectionString: string): {
  db: ReturnType<typeof drizzle<typeof schema>>;
  sql: ReturnType<typeof postgres>;
  close: () => Promise<void>;
} {
  const sql = postgres(connectionString, {
    max: 10,
    idle_timeout: 30,
    prepare: false,
  });
  const db = drizzle(sql, { schema });
  return {
    db,
    sql,
    close: async () => {
      await sql.end({ timeout: 5 });
    },
  };
}

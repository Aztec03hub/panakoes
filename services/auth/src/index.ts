/**
 * Auth service entrypoint. Loads config (fail-fast on bad env), builds the
 * Hono app, and binds it to the configured port via `@hono/node-server`.
 */
import { serve } from "@hono/node-server";

import { loadConfig } from "./config.ts";
import { createDatabase } from "./db/client.ts";
import { createLogger } from "./logger.ts";
import { createServer } from "./server.ts";

async function main(): Promise<void> {
  const config = loadConfig();
  const logger = createLogger(config);
  const { db } = createDatabase(config.DATABASE_URL);

  const app = createServer({ db, config, logger });

  serve({ fetch: app.fetch, port: config.PORT, hostname: "0.0.0.0" }, (info) => {
    logger.info({ port: info.port }, "auth service listening");
  });
}

main().catch((err) => {
  process.stderr.write(
    `auth service failed to start: ${err instanceof Error ? err.stack : String(err)}\n`,
  );
  process.exit(1);
});

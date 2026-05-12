/**
 * Auth service entrypoint. Loads config (fail-fast on bad env), builds the
 * Hono app, and binds it to the configured port via `@hono/node-server`.
 *
 * When `AUTH_JWT_ALGORITHM=RS256` (ADR-041 phase 1, opt-in), the entrypoint
 * also constructs a KMS client + KmsSigner pointed at `AUTH_JWT_KMS_KEY_ID`
 * and threads it into the server so RS256 sign + verify and the JWKS
 * endpoint pick it up. HS256 (default) keeps the legacy path.
 */

import { KMSClient } from "@aws-sdk/client-kms";
import { serve } from "@hono/node-server";

import { createKmsSigner, type KmsSigner } from "./auth/kms-signer.ts";
import { loadConfig } from "./config.ts";
import { createDatabase } from "./db/client.ts";
import { createLogger } from "./logger.ts";
import { createServer } from "./server.ts";

async function main(): Promise<void> {
  const config = loadConfig();
  const logger = createLogger(config);
  const { db } = createDatabase(config.DATABASE_URL);

  let kmsSigner: KmsSigner | undefined;
  if (config.AUTH_JWT_ALGORITHM === "RS256") {
    const client = new KMSClient({ region: config.AWS_REGION });
    // Non-null assertion is sound: config-load's `refine` enforces that
    // AUTH_JWT_KMS_KEY_ID is non-empty whenever the algorithm is RS256.
    kmsSigner = createKmsSigner(client, { keyId: config.AUTH_JWT_KMS_KEY_ID as string });
    logger.info({ region: config.AWS_REGION }, "auth service signing with RS256 via AWS KMS");
  }

  const app = createServer({ db, config, logger, kmsSigner });

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

/**
 * Structured logging via pino.
 *
 * Logs are JSON in production (CloudWatch-friendly) and pretty-printed in
 * development if `pino-pretty` happens to be installed. Per ADR-015 logs
 * flow to CloudWatch then archive to S3; structured format is a hard
 * requirement for that pipeline.
 */
import { type Logger as PinoLogger, pino } from "pino";

import type { Config } from "./config.ts";

// pino 10 dropped the `pino.Logger` namespace type export; the type is now
// a top-level named export. Re-export under our local alias so the rest of
// auth keeps importing { Logger } from "./logger.ts".
export type Logger = PinoLogger;

export function createLogger(config: Pick<Config, "LOG_LEVEL" | "NODE_ENV">): Logger {
  return pino({
    level: config.LOG_LEVEL,
    base: {
      service: "auth",
      env: config.NODE_ENV,
    },
    timestamp: pino.stdTimeFunctions.isoTime,
    redact: {
      paths: ["req.headers.authorization", "req.headers.cookie", "password", "*.password"],
      censor: "[REDACTED]",
    },
  });
}

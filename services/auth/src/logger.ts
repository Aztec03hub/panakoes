/**
 * Structured logging via pino.
 *
 * Logs are JSON in production (CloudWatch-friendly) and pretty-printed in
 * development if `pino-pretty` happens to be installed. Per ADR-015 logs
 * flow to CloudWatch then archive to S3; structured format is a hard
 * requirement for that pipeline.
 */
import { pino } from "pino";

import type { Config } from "./config.ts";

export type Logger = pino.Logger;

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

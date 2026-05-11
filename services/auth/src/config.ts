/**
 * Environment-driven configuration for the Auth service.
 *
 * All values come from environment variables, validated via zod at startup.
 * The service refuses to boot if any required value is missing or invalid;
 * fail-fast beats degraded behaviour in a security-critical path.
 */
import { z } from "zod";

const ConfigSchema = z.object({
  PORT: z.coerce.number().int().positive().default(8080),
  LOG_LEVEL: z.enum(["trace", "debug", "info", "warn", "error", "fatal"]).default("info"),
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),

  DATABASE_URL: z.string().url(),

  AUTH_JWT_SECRET: z
    .string()
    .min(32, "AUTH_JWT_SECRET must be at least 32 bytes for HS256 signing"),
  AUTH_JWT_ISSUER: z.string().url().default("https://auth.panakoes.com"),
  AUTH_JWT_AUDIENCE: z.string().min(1).default("panakoes-api"),
  AUTH_JWT_EXPIRES_IN_SECONDS: z.coerce.number().int().positive().default(3600),

  BETTER_AUTH_URL: z.string().url().default("http://localhost:8080"),

  // Plan-claim lookup: the auth service reads the user's active subscription
  // from the `panakoes-dev-subscriptions` DynamoDB table at sign-in time so
  // the minted JWT carries an accurate `plan` claim. AWS_REGION defaults to
  // us-east-1 to match `infra/dev/`; the table name is overridable for
  // multi-env / testing without rebuilding the image.
  AWS_REGION: z.string().min(1).default("us-east-1"),
  DDB_SUBSCRIPTIONS_TABLE: z.string().min(1).default("panakoes-dev-subscriptions"),
});

export type Config = z.infer<typeof ConfigSchema>;

/**
 * Parse and validate process.env (or any caller-supplied env). Throws a
 * `ZodError` with structured issues if validation fails; callers should let
 * that bubble up at startup so the service refuses to boot on bad config.
 */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return ConfigSchema.parse(env);
}

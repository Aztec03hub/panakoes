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

  // Email verification (v0.1 non-enforcing: claim is surfaced but unverified
  // users can still sign in; a future ADR-XX will flip enforcement on).
  EMAIL_VERIFICATION_BASE_URL: z
    .string()
    .url()
    .default("https://api.dev.panakoes.com/v1/auth/verify-email"),
  EMAIL_VERIFICATION_TTL_SECONDS: z.coerce.number().int().positive().default(3600),

  // SES sender identity. The domain `lafayettelabs.com` is pending DKIM
  // verification (PR #265). The verified-address path works pre-DKIM.
  SES_FROM_ADDRESS: z.string().email().default("noreply@lafayettelabs.com"),
  SES_REPLY_TO_ADDRESS: z.string().email().default("phil@lafayettelabs.com"),
  SES_REGION: z.string().min(1).default("us-east-1"),

  // `disabled` skips SES entirely (in-memory capture). Used by tests and by
  // local dev where AWS credentials are absent. `ses` is the production
  // mode that calls SES via the AWS SDK.
  EMAIL_SENDER_MODE: z.enum(["ses", "disabled"]).default("disabled"),
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

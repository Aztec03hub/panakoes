/**
 * Environment-driven configuration for the Auth service.
 *
 * All values come from environment variables, validated via zod at startup.
 * The service refuses to boot if any required value is missing or invalid;
 * fail-fast beats degraded behaviour in a security-critical path.
 */
import { z } from "zod";

const ConfigSchema = z
  .object({
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

    /**
     * Signing algorithm. HS256 (default, phase 1) keeps the existing
     * shared-secret path. RS256 (phase 2 path, opt-in today) routes signing
     * through AWS KMS via `AUTH_JWT_KMS_KEY_ID` and exposes the public key
     * at `/.well-known/jwks.json`. See ADR-041.
     */
    AUTH_JWT_ALGORITHM: z.enum(["HS256", "RS256"]).default("HS256"),
    /**
     * KMS key id, alias, or ARN used for RS256 signing. Required only when
     * `AUTH_JWT_ALGORITHM=RS256`; ignored when HS256 (validated below).
     */
    AUTH_JWT_KMS_KEY_ID: z.string().optional(),
    /**
     * AWS region for the KMS client. Defaults to `us-east-1`; in dev/prod
     * this is set on the ECS task definition (see infra/dev/ecs/main.tf
     * which already injects `AWS_REGION`).
     */
    AWS_REGION: z.string().default("us-east-1"),

    BETTER_AUTH_URL: z.string().url().default("http://localhost:8080"),
  })
  .refine(
    (cfg) => cfg.AUTH_JWT_ALGORITHM !== "RS256" || (cfg.AUTH_JWT_KMS_KEY_ID?.length ?? 0) > 0,
    {
      message: "AUTH_JWT_KMS_KEY_ID is required when AUTH_JWT_ALGORITHM=RS256",
      path: ["AUTH_JWT_KMS_KEY_ID"],
    },
  );

export type Config = z.infer<typeof ConfigSchema>;

/**
 * Parse and validate process.env (or any caller-supplied env). Throws a
 * `ZodError` with structured issues if validation fails; callers should let
 * that bubble up at startup so the service refuses to boot on bad config.
 */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  return ConfigSchema.parse(env);
}

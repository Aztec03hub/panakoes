/**
 * Better-Auth configuration.
 *
 * Database: PostgreSQL via the Drizzle adapter.
 * Provider: email + password (Argon2id hashing per Better-Auth defaults).
 * Sessions: database-backed via the `session` table.
 *
 * The Better-Auth handler is mounted in `server.ts` for any flows we want to
 * delegate (signup/signin internally call `auth.api.signUpEmail` /
 * `auth.api.signInEmail` so we can decorate the response with our JWT).
 */
import { type BetterAuthOptions, betterAuth } from "better-auth";
import { drizzleAdapter } from "better-auth/adapters/drizzle";

import type { Config } from "../config.ts";
import type { Database } from "../db/client.ts";
import { schema } from "../db/schema.ts";

export type AuthInstance = ReturnType<typeof createAuth>;

export function createAuth(db: Database["db"], config: Config) {
  const options: BetterAuthOptions = {
    baseURL: config.BETTER_AUTH_URL,
    secret: config.AUTH_JWT_SECRET,
    database: drizzleAdapter(db, {
      provider: "pg",
      schema,
    }),
    emailAndPassword: {
      enabled: true,
      autoSignIn: true,
      minPasswordLength: 8,
      maxPasswordLength: 128,
    },
    session: {
      expiresIn: config.AUTH_JWT_EXPIRES_IN_SECONDS,
      updateAge: Math.floor(config.AUTH_JWT_EXPIRES_IN_SECONDS / 2),
    },
    rateLimit: {
      enabled: true,
      window: 60,
      max: 30,
    },
    advanced: {
      database: {
        generateId: false,
      },
    },
  };
  return betterAuth(options);
}

/**
 * Better-Auth client integration stub.
 *
 * The auth service (services/auth) issues HS256 JWTs today via
 * POST /auth/sign-in. Once we add Better-Auth's official Svelte client, this
 * module should expose:
 *   - signIn(email, password): calls POST /auth/sign-in, stores the JWT.
 *   - signOut(): clears stored JWT, calls POST /auth/sign-out for revocation.
 *   - getSession(): returns the decoded JWT payload or null.
 *   - requireAdmin(): redirects to /login if the session lacks the admin role.
 *
 * For v0.1-skeleton we only sketch the shape so layouts and pages compile.
 * The placeholders return either nulls or `not implemented` rejected promises
 * so accidental use surfaces immediately in dev.
 */

export interface AdminSession {
  user_id: string;
  email: string;
  role: "admin" | "user";
  expires_at: string;
}

const NOT_IMPLEMENTED = "auth client not implemented yet (v0.1 skeleton)";

/** Sign in with email + password. Stub: rejects until wired to Better-Auth. */
export async function signIn(_email: string, _password: string): Promise<AdminSession> {
  return Promise.reject(new Error(NOT_IMPLEMENTED));
}

/** Sign out the current session. Stub: no-op until wired. */
export async function signOut(): Promise<void> {
  return Promise.resolve();
}

/** Returns the current session if one exists, else null. Stub returns null. */
export function getSession(): AdminSession | null {
  return null;
}

/** Returns true if the current session has admin role. Stub returns false. */
export function isAdmin(): boolean {
  return false;
}

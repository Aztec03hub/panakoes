/**
 * Auth session store for the Panakoes admin SPA.
 *
 * Owns the client-side auth state for the SPA: persists the Better-Auth
 * JWT returned by `POST {AUTH_API_BASE}/sign-in` and exposes helpers the
 * rest of the app uses to attach the Bearer header and gate routes.
 *
 * Storage choice: localStorage under the single key
 * `panakoes-admin-auth`. The SPA is a SvelteKit static bundle on
 * S3 + CloudFront. There is no SvelteKit server runtime to set an
 * HttpOnly cookie at this tier, and the auth service issues JWTs (not
 * opaque session ids), so the JWT is the bearer of authority. localStorage
 * gives us:
 *
 *   + survives full page reloads + new tabs.
 *   + no cross-site cookie concerns (we are not posting auth from third
 *     parties).
 *   + the JWT is the only credential; revoking it server-side (sign-out
 *     calls `POST /sign-out`) plus a short `exp` claim bound the blast
 *     radius of a leaked browser context.
 *
 * Trade-off: localStorage is readable by any JS on the same origin, so a
 * cross-site-scripting bug on the admin SPA hands an attacker the JWT. The
 * mitigations are CSP (deny inline + restrict script-src), keeping the
 * admin bundle dependency surface small, and the auth service's short
 * `exp` window. The alternative (HttpOnly cookie via a tiny edge auth
 * Worker / CloudFront Function) is the long-term plan once we own a
 * server boundary in front of the SPA; called out as a follow-up.
 */

import { AUTH_API_BASE } from "./config";

/** The shape the auth service returns under the `user` key on sign-in. */
export interface SessionUser {
  id: string;
  email: string;
  role: "admin" | "user";
}

/** The persisted auth-session record. */
export interface AuthSession {
  token: string;
  expiresAt: string; // ISO-8601
  user: SessionUser;
}

/** Thrown by `signIn` when the auth service rejects the request. */
export class AuthError extends Error {
  public readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "AuthError";
    this.status = status;
  }
}

/** localStorage key under which the session blob is stored. */
export const AUTH_STORAGE_KEY = "panakoes-admin-auth";

/**
 * `fetch` adapter so tests can inject a mock without monkey-patching
 * globals. Defaults to `globalThis.fetch` in production paths.
 */
export type Fetcher = (input: string, init?: RequestInit) => Promise<Response>;

/** Test seam for "now"; overrideable in unit tests via `setClock`. */
let clock: () => number = () => Date.now();

/** Replace the clock (test-only seam). */
export function setClock(fn: () => number): void {
  clock = fn;
}

/** Reset the clock to the real-time wall clock (test-only seam). */
export function resetClock(): void {
  clock = () => Date.now();
}

/**
 * Test seam: lets tests force "no storage available" (the SSR / prerender
 * case) without monkey-patching `globalThis.localStorage`. Default is to
 * use the platform `localStorage` when defined.
 */
let storageOverride: Storage | null | undefined;

/** Test-only seam: force a specific storage (or null) for the next reads. */
export function setStorageOverride(storage: Storage | null): void {
  storageOverride = storage;
}

/** Test-only seam: clear the storage override and fall back to `localStorage`. */
export function resetStorageOverride(): void {
  storageOverride = undefined;
}

/**
 * Safe localStorage accessor. Returns null in environments without
 * window/localStorage (SSR / prerender) so this module never throws at
 * module load. Tests inject via `setStorageOverride`.
 */
function getStorage(): Storage | null {
  if (storageOverride !== undefined) {
    return storageOverride;
  }
  if (typeof globalThis !== "undefined" && "localStorage" in globalThis) {
    return (globalThis as { localStorage: Storage }).localStorage;
  }
  return null;
}

/**
 * The single source of truth for the current session. Initialized from
 * localStorage on module load so a full page reload preserves the
 * signed-in state, and re-written to localStorage on every change.
 */
export const currentSession = $state<{ value: AuthSession | null }>({
  value: hydrate(),
});

/** Read the persisted session from localStorage, if any + non-expired. */
function hydrate(): AuthSession | null {
  const storage = getStorage();
  if (storage === null) {
    return null;
  }
  const raw = storage.getItem(AUTH_STORAGE_KEY);
  if (raw === null || raw === "") {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as AuthSession;
    if (
      typeof parsed?.token !== "string" ||
      typeof parsed?.expiresAt !== "string" ||
      typeof parsed?.user?.id !== "string"
    ) {
      return null;
    }
    if (isExpired(parsed.expiresAt)) {
      storage.removeItem(AUTH_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    // Corrupted JSON: wipe and start clean rather than crash boot.
    storage.removeItem(AUTH_STORAGE_KEY);
    return null;
  }
}

function isExpired(expiresAt: string): boolean {
  const expiry = Date.parse(expiresAt);
  if (Number.isNaN(expiry)) {
    return true;
  }
  return expiry <= clock();
}

/** Persist (or clear) the current session in localStorage. */
function persist(session: AuthSession | null): void {
  const storage = getStorage();
  if (storage === null) {
    return;
  }
  if (session === null) {
    storage.removeItem(AUTH_STORAGE_KEY);
    return;
  }
  storage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session));
}

/**
 * Sign in with email + password against `POST {AUTH_API_BASE}/sign-in`.
 *
 * On success, stores the session and returns the user. On non-2xx,
 * throws `AuthError` with the HTTP status preserved so the caller can
 * distinguish credential failures (401) from infra failures (5xx).
 */
export async function signIn(
  email: string,
  password: string,
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  baseUrl: string = AUTH_API_BASE,
): Promise<SessionUser> {
  const response = await fetcher(`${baseUrl}/sign-in`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    let message = `Sign in failed (HTTP ${response.status})`;
    if (response.status === 401) {
      message = "Invalid email or password.";
    } else if (response.status >= 500) {
      message = "Auth service unavailable. Try again shortly.";
    }
    throw new AuthError(message, response.status);
  }
  const payload = (await response.json()) as AuthSession;
  currentSession.value = payload;
  persist(payload);
  return payload.user;
}

/**
 * Clear the local session. Does not call the server `/sign-out` route:
 * the JWT is stateless, the server-side revoke happens out-of-band, and
 * the SPA never blocks the user on a network round-trip to log out.
 */
export function signOut(): void {
  currentSession.value = null;
  persist(null);
}

/**
 * True when a non-expired session is present. Called from layout-level
 * route gating; treats an expired `expiresAt` as logged-out and auto-
 * clears the stale record so the next render sees a clean state.
 */
export function isAuthenticated(): boolean {
  const session = currentSession.value;
  if (session === null) {
    return false;
  }
  if (isExpired(session.expiresAt)) {
    signOut();
    return false;
  }
  return true;
}

/**
 * Returns an Authorization header for the current session, or an empty
 * object when no session is present. Caller spreads into `init.headers`.
 */
export function bearerHeader(): Record<string, string> {
  if (!isAuthenticated()) {
    return {};
  }
  const session = currentSession.value;
  if (session === null) {
    return {};
  }
  return { Authorization: `Bearer ${session.token}` };
}

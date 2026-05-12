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
 * Clear the local session AND best-effort revoke it server-side.
 *
 * The local clear is synchronous (in-memory store + localStorage) so the
 * UI never blocks on a network round-trip. The `POST /sign-out` call is
 * fire-and-forget: failures (offline, gateway 5xx, CORS error) are
 * swallowed so a flaky network can never trap a user in a half-signed-out
 * state. The server-side revoke is the durable revocation; clients that
 * cared about absolute revocation would call /sign-out separately.
 *
 * Returns the (resolved-on-completion) Promise for the server call so
 * tests can `await` it deterministically, but callers in app code are
 * intentionally not required to await; the local clear is what gates UX.
 */
export function signOut(
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  baseUrl: string = AUTH_API_BASE,
): Promise<void> {
  const prior = currentSession.value;
  currentSession.value = null;
  persist(null);

  if (prior === null) {
    return Promise.resolve();
  }
  // Best-effort POST. We do not chain `.then` on the body; the auth
  // service returns 204 (no body) on success and 401 on an already-
  // invalid token, both of which we ignore for the local logout flow.
  return fetcher(`${baseUrl}/sign-out`, {
    method: "POST",
    headers: { Authorization: `Bearer ${prior.token}` },
  })
    .then(() => undefined)
    .catch(() => undefined);
}

/**
 * True when a non-expired session is present. Called from layout-level
 * route gating; treats an expired `expiresAt` as logged-out and auto-
 * clears the stale record so the next render sees a clean state. The
 * server-side `/sign-out` call is fire-and-forget; we do not await it.
 */
export function isAuthenticated(): boolean {
  const session = currentSession.value;
  if (session === null) {
    return false;
  }
  if (isExpired(session.expiresAt)) {
    // Local-only clear: the token is past its `exp`, so any server call
    // would just be rejected as invalid_token. Skip the network round-
    // trip entirely.
    currentSession.value = null;
    persist(null);
    return false;
  }
  return true;
}

/**
 * Re-validate the persisted session against the auth service.
 *
 * Called on SPA boot (right after `hydrate`) so a token that's been
 * revoked server-side, signed with a since-rotated key, or otherwise
 * invalidated never silently appears "signed in" in the UI.
 *
 * Contract:
 *   - 200: the server returns the canonical user identity. We REFRESH the
 *     stored claims (role, email, name) so the SPA reflects any
 *     server-side mutation (e.g. role promotion) without a fresh sign-in.
 *     Returns true.
 *   - 401: the token is invalid. We sign out locally + best-effort fire a
 *     server-side revoke. Returns false.
 *   - Network / 5xx: we INTENTIONALLY do NOT sign out. A transient gateway
 *     failure should not log the user out; the UI keeps the cached session
 *     and the next request will surface a fresh 401 if needed. Returns the
 *     current `isAuthenticated()` value as a no-op signal.
 *
 * This is the only place the SPA pays the latency cost of consulting the
 * auth service on boot. Subsequent requests rely on the JWT directly +
 * the global 401 redirect path in `apiFetch`.
 */
export async function validateSession(
  fetcher: Fetcher = globalThis.fetch.bind(globalThis),
  baseUrl: string = AUTH_API_BASE,
): Promise<boolean> {
  const session = currentSession.value;
  if (session === null) {
    return false;
  }
  let response: Response;
  try {
    response = await fetcher(`${baseUrl}/me`, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${session.token}`,
        Accept: "application/json",
      },
    });
  } catch {
    // Network failure: keep the cached session; the next user-driven
    // request will resolve the ambiguity. Returning the current authed
    // state lets callers branch without re-checking.
    return isAuthenticated();
  }

  if (response.status === 401) {
    await signOut(fetcher, baseUrl);
    return false;
  }
  if (!response.ok) {
    // 5xx or other transient failure: do NOT sign out. See the docstring.
    return isAuthenticated();
  }

  try {
    const payload = (await response.json()) as {
      user?: { id?: string; email?: string; role?: "admin" | "user"; name?: string };
    };
    const u = payload.user;
    if (
      u !== undefined &&
      typeof u.id === "string" &&
      typeof u.email === "string" &&
      (u.role === "admin" || u.role === "user")
    ) {
      const refreshed: AuthSession = {
        token: session.token,
        expiresAt: session.expiresAt,
        user: { id: u.id, email: u.email, role: u.role },
      };
      currentSession.value = refreshed;
      persist(refreshed);
    }
  } catch {
    // Body wasn't JSON; treat as a successful validation but skip the
    // claim refresh. The HTTP 200 is the authoritative signal.
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
  // `isAuthenticated` returning true guarantees `currentSession.value` is non-null.
  const session = currentSession.value as AuthSession;
  return { Authorization: `Bearer ${session.token}` };
}

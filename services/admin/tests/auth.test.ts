import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AUTH_STORAGE_KEY,
  AuthError,
  bearerHeader,
  currentSession,
  isAuthenticated,
  resetClock,
  resetStorageOverride,
  setClock,
  setStorageOverride,
  signIn,
  signOut,
  validateSession,
} from "../src/lib/auth.svelte";

const FIXED_NOW = Date.parse("2026-05-11T12:00:00Z");
const FUTURE = new Date(FIXED_NOW + 60 * 60 * 1000).toISOString();
const PAST = new Date(FIXED_NOW - 60 * 1000).toISOString();

const validSession = {
  token: "jwt.abc.def",
  expiresAt: FUTURE,
  user: { id: "u_1", email: "phil@lafayettelabs.com", role: "admin" as const },
};

function okJson<T>(payload: T): Response {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function errResp(status: number): Response {
  return new Response("err", { status });
}

beforeEach(() => {
  // Fake the system clock so any code path that consults `Date.now()`
  // (the SUT's default clock fn, plus module-init `hydrate()` after a
  // `vi.resetModules()` dynamic re-import) sees FIXED_NOW. Without this,
  // real wall-clock drift past `FUTURE` (FIXED_NOW + 1h) would cause the
  // persisted session to be classified expired during hydration, and the
  // test would only pass for one wall-clock hour per day. See
  // services/admin/README.md "Time-dependent tests".
  vi.useFakeTimers();
  vi.setSystemTime(new Date(FIXED_NOW));
  globalThis.localStorage.clear();
  currentSession.value = null;
  setClock(() => FIXED_NOW);
});

afterEach(() => {
  resetClock();
  vi.useRealTimers();
});

describe("signIn", () => {
  it("happy path: stores the session and returns the user", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(okJson(validSession));
    const user = await signIn("phil@lafayettelabs.com", "pw", fetcher, "https://auth.example.com");
    expect(user).toEqual(validSession.user);
    expect(currentSession.value).toEqual(validSession);
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBe(JSON.stringify(validSession));
    expect(fetcher).toHaveBeenCalledWith("https://auth.example.com/sign-in", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ email: "phil@lafayettelabs.com", password: "pw" }),
    });
  });

  it("rejects with AuthError on 401 and preserves the status", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errResp(401));
    await expect(signIn("p@x", "wrong", fetcher, "https://a")).rejects.toMatchObject({
      name: "AuthError",
      status: 401,
      message: "Invalid email or password.",
    });
    expect(currentSession.value).toBeNull();
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("rejects with friendly message on 5xx", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errResp(503));
    await expect(signIn("p@x", "pw", fetcher, "https://a")).rejects.toMatchObject({
      status: 503,
      message: "Auth service unavailable. Try again shortly.",
    });
  });

  it("rejects with generic message on other 4xx", async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(errResp(400));
    await expect(signIn("p@x", "pw", fetcher, "https://a")).rejects.toMatchObject({
      status: 400,
      message: "Sign in failed (HTTP 400)",
    });
  });

  it("AuthError is an Error subclass", () => {
    const e = new AuthError("x", 401);
    expect(e).toBeInstanceOf(Error);
    expect(e.name).toBe("AuthError");
    expect(e.status).toBe(401);
  });
});

describe("signOut", () => {
  it("clears the session and localStorage immediately (best-effort server call)", async () => {
    currentSession.value = validSession;
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(validSession));
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(null, { status: 204 }));
    await signOut(fetcher, "https://auth.example.com");
    expect(currentSession.value).toBeNull();
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
    expect(fetcher).toHaveBeenCalledWith("https://auth.example.com/sign-out", {
      method: "POST",
      headers: { Authorization: `Bearer ${validSession.token}` },
    });
  });

  it("swallows server-side sign-out errors so logout always completes locally", async () => {
    currentSession.value = validSession;
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(validSession));
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("network down"));
    await signOut(fetcher, "https://auth.example.com");
    expect(currentSession.value).toBeNull();
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("skips the server call entirely when no session was present", async () => {
    currentSession.value = null;
    const fetcher = vi.fn();
    await signOut(fetcher, "https://auth.example.com");
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("isAuthenticated", () => {
  it("false when no session", () => {
    expect(isAuthenticated()).toBe(false);
  });

  it("true when non-expired session present", () => {
    currentSession.value = validSession;
    expect(isAuthenticated()).toBe(true);
  });

  it("false + auto-signOut when session is expired", () => {
    const expired = { ...validSession, expiresAt: PAST };
    currentSession.value = expired;
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(expired));
    expect(isAuthenticated()).toBe(false);
    expect(currentSession.value).toBeNull();
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("false when expiresAt is unparseable", () => {
    currentSession.value = { ...validSession, expiresAt: "not-a-date" };
    expect(isAuthenticated()).toBe(false);
  });
});

describe("bearerHeader", () => {
  it("returns Authorization header when session present", () => {
    currentSession.value = validSession;
    expect(bearerHeader()).toEqual({ Authorization: `Bearer ${validSession.token}` });
  });

  it("returns empty object when no session", () => {
    expect(bearerHeader()).toEqual({});
  });

  it("returns empty object and clears state when session is expired", () => {
    currentSession.value = { ...validSession, expiresAt: PAST };
    expect(bearerHeader()).toEqual({});
    expect(currentSession.value).toBeNull();
  });
});

describe("hydration via dynamic import", () => {
  it("loads a valid persisted session at module init", async () => {
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(validSession));
    vi.resetModules();
    // The global beforeEach has already pinned Date.now via fake timers,
    // so the re-imported module's hydrate() sees FIXED_NOW.
    const mod = await import("../src/lib/auth.svelte");
    expect(mod.currentSession.value).toEqual(validSession);
  });

  it("ignores an expired persisted session and wipes it", async () => {
    const expired = { ...validSession, expiresAt: PAST };
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(expired));
    vi.resetModules();
    const mod = await import("../src/lib/auth.svelte");
    expect(mod.currentSession.value).toBeNull();
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("ignores corrupted JSON in localStorage and wipes it", async () => {
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, "{not valid json");
    vi.resetModules();
    const mod = await import("../src/lib/auth.svelte");
    expect(mod.currentSession.value).toBeNull();
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("ignores a session blob missing required fields", async () => {
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ token: 123 }));
    vi.resetModules();
    const mod = await import("../src/lib/auth.svelte");
    expect(mod.currentSession.value).toBeNull();
  });

  it("returns null hydration when localStorage is empty", async () => {
    vi.resetModules();
    const mod = await import("../src/lib/auth.svelte");
    expect(mod.currentSession.value).toBeNull();
  });
});

describe("storage unavailable (SSR / prerender)", () => {
  afterEach(() => {
    resetStorageOverride();
  });

  it("signIn succeeds and only updates the in-memory store when storage is null", async () => {
    setStorageOverride(null);
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(validSession), { status: 200 }));
    const user = await signIn("a@b.c", "pw", fetcher, "https://a");
    expect(user).toEqual(validSession.user);
    expect(currentSession.value).toEqual(validSession);
  });

  it("signOut clears in-memory state even with null storage", () => {
    setStorageOverride(null);
    currentSession.value = validSession;
    signOut();
    expect(currentSession.value).toBeNull();
  });

  it("hydration returns null when no storage is available", async () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
    // biome-ignore lint/performance/noDelete: temporary test seam removed in finally
    delete (globalThis as { localStorage?: Storage }).localStorage;
    try {
      vi.resetModules();
      const mod = await import("../src/lib/auth.svelte");
      expect(mod.currentSession.value).toBeNull();
    } finally {
      if (original !== undefined) {
        Object.defineProperty(globalThis, "localStorage", original);
      }
    }
  });

  it("bearerHeader returns empty object when storage null and no session", () => {
    setStorageOverride(null);
    expect(bearerHeader()).toEqual({});
  });
});

describe("validateSession", () => {
  it("returns false when no session is present", async () => {
    currentSession.value = null;
    const fetcher = vi.fn();
    expect(await validateSession(fetcher, "https://a")).toBe(false);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("returns true + refreshes stored claims when /auth/me returns 200", async () => {
    currentSession.value = validSession;
    const fetcher = vi.fn().mockResolvedValueOnce(
      okJson({
        user: { id: "u_1", email: "phil@lafayettelabs.com", name: "phil", role: "user" },
      }),
    );
    const ok = await validateSession(fetcher, "https://a");
    expect(ok).toBe(true);
    expect(fetcher).toHaveBeenCalledWith("https://a/auth/me", {
      method: "GET",
      headers: {
        Authorization: `Bearer ${validSession.token}`,
        Accept: "application/json",
      },
    });
    // Role got refreshed from admin -> user per the server response.
    expect(currentSession.value?.user.role).toBe("user");
  });

  it("signs out locally and returns false on 401", async () => {
    currentSession.value = validSession;
    globalThis.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(validSession));
    const fetcher = vi
      .fn()
      // first call: /auth/me -> 401
      .mockResolvedValueOnce(new Response(null, { status: 401 }))
      // second call (from signOut): /sign-out best-effort
      .mockResolvedValueOnce(new Response(null, { status: 401 }));
    const ok = await validateSession(fetcher, "https://a");
    expect(ok).toBe(false);
    expect(currentSession.value).toBeNull();
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBeNull();
  });

  it("preserves the session and returns isAuthenticated() on 5xx", async () => {
    currentSession.value = validSession;
    const fetcher = vi.fn().mockResolvedValueOnce(new Response(null, { status: 503 }));
    const ok = await validateSession(fetcher, "https://a");
    expect(ok).toBe(true);
    // Session NOT cleared.
    expect(currentSession.value).toEqual(validSession);
  });

  it("preserves the session and returns isAuthenticated() on network error", async () => {
    currentSession.value = validSession;
    const fetcher = vi.fn().mockRejectedValueOnce(new Error("network down"));
    const ok = await validateSession(fetcher, "https://a");
    expect(ok).toBe(true);
    expect(currentSession.value).toEqual(validSession);
  });

  it("treats a 200 with an unparseable body as success without refreshing claims", async () => {
    currentSession.value = validSession;
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        new Response("not-json", { status: 200, headers: { "Content-Type": "text/plain" } }),
      );
    const ok = await validateSession(fetcher, "https://a");
    expect(ok).toBe(true);
    expect(currentSession.value).toEqual(validSession);
  });

  it("ignores a 200 body whose user shape is invalid (does not refresh claims)", async () => {
    currentSession.value = validSession;
    const fetcher = vi.fn().mockResolvedValueOnce(okJson({ user: { id: 123, role: "weird" } }));
    const ok = await validateSession(fetcher, "https://a");
    expect(ok).toBe(true);
    // Stored claims are unchanged because the server payload was unusable.
    expect(currentSession.value).toEqual(validSession);
  });
});

describe("clock default", () => {
  it("real Date.now is used after resetClock (default clock fn)", () => {
    resetClock();
    // A future session must still be valid against the real wall clock.
    const future = new Date(Date.now() + 60 * 60 * 1000).toISOString();
    currentSession.value = { ...validSession, expiresAt: future };
    expect(isAuthenticated()).toBe(true);
  });
});

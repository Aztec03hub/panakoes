import { fireEvent, render, screen } from "@testing-library/svelte";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Login from "../src/routes/login/+page.svelte";
import { AUTH_STORAGE_KEY, currentSession, resetClock, setClock } from "../src/lib/auth.svelte";

const validSession = {
  token: "jwt.t.t",
  expiresAt: "2099-01-01T00:00:00Z",
  user: { id: "u_1", email: "phil@lafayettelabs.com", role: "admin" as const },
};

beforeEach(() => {
  globalThis.localStorage.clear();
  currentSession.value = null;
  setClock(() => Date.parse("2026-05-11T12:00:00Z"));
  // Reset the global fetch mock per-test.
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  resetClock();
  vi.unstubAllGlobals();
});

describe("Login form", () => {
  it("renders the sign-in form with email + password inputs", () => {
    render(Login);
    expect(screen.getByRole("button", { name: /sign in/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });

  it("happy path: signs in and persists the session", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify(validSession), { status: 200 }),
    );
    render(Login);
    const email = screen.getByLabelText(/email/i) as HTMLInputElement;
    const password = screen.getByLabelText(/password/i) as HTMLInputElement;
    await fireEvent.input(email, { target: { value: "phil@lafayettelabs.com" } });
    await fireEvent.input(password, { target: { value: "hunter2" } });
    const form = email.closest("form");
    expect(form).not.toBeNull();
    if (form !== null) {
      await fireEvent.submit(form);
    }
    // Allow the awaited signIn + goto to settle.
    await new Promise((r) => setTimeout(r, 0));
    expect(currentSession.value).toEqual(validSession);
    expect(globalThis.localStorage.getItem(AUTH_STORAGE_KEY)).toBe(JSON.stringify(validSession));
  });

  it("renders the typed error message on 401", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response("", { status: 401 }),
    );
    render(Login);
    const email = screen.getByLabelText(/email/i) as HTMLInputElement;
    const password = screen.getByLabelText(/password/i) as HTMLInputElement;
    await fireEvent.input(email, { target: { value: "phil@x.com" } });
    await fireEvent.input(password, { target: { value: "wrong" } });
    const form = email.closest("form");
    if (form !== null) {
      await fireEvent.submit(form);
    }
    await new Promise((r) => setTimeout(r, 0));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(currentSession.value).toBeNull();
  });
});

import "@testing-library/jest-dom/vitest";
import { readable } from "svelte/store";
import { vi } from "vitest";

// `$app/navigation` is resolved at runtime by SvelteKit. In unit tests we
// stub it to a no-op so modules that import `goto` (e.g. `lib/api.ts`'s
// `apiFetch` 401 redirect path) can be imported + executed under vitest
// without booting the SvelteKit runtime. The few tests that need to
// assert against `goto` inject their own `navigate` dep via `apiFetch`'s
// deps arg.
vi.mock("$app/navigation", () => ({
  goto: vi.fn(async () => undefined),
}));

vi.mock("$app/stores", () => ({
  page: readable({
    url: new URL("http://localhost/"),
    params: {},
    route: { id: null },
  }),
}));

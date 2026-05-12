---
category: Added
---

- `services/admin`: `data-testid="sign-out-button"` on the layout sign-out button to enable stable e2e selectors.
- `services/admin`: `Button` component migrated to Svelte 5 runes (`$props()` + `$derived` + `Snippet`) and now accepts arbitrary HTML attributes via spread, so callers can pass `data-testid`, `aria-*`, `id`, etc. without each needing an explicit prop.
- `services/admin`: replaced legacy `on:click` with `onclick` on the forbidden-page sign-out button to match the runes-mode contract.

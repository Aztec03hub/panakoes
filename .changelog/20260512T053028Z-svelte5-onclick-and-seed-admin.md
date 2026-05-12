---
category: Fixed
---

- `services/admin`: Sign Out button and Lifecycle Reset button now actually fire their handlers. Svelte 5 in runes mode treats `on:click` event forwarding through nested components as unreliable; all in-tree call sites are migrated to the native `onclick={...}` prop and the shared `Button` component passes `onclick` through to the underlying DOM element.
- `services/auth`: new `make seed-admin EMAIL=foo@example.com` target promotes an existing user to `role=admin` via ECS exec into a running auth task. Idempotent (exits 0 if the user is already an admin). See `docs/runbooks/seed-admin.md`.

---
category: Fixed
---

- `services/admin/src/routes/realtime/+page.svelte`: replace explicit `onDestroy` import with the Svelte 5 idiomatic `$effect(() => () => cleanup)`. The `onDestroy` call was triggering a `Cannot read properties of null (reading 'r')` TypeError during the route's reactive setup, leaving `/realtime` rendering as a blank page on admin.panakoes.com. The `$effect` cleanup hook runs in the right reactive scope without the hazard.

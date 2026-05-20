---
category: Fixed
---

- `services/admin`: `/realtime` record button now resets a dead session (failed or ended state) on click instead of silently no-oping; users can start a fresh recording after a WebSocket failure without reloading the page.

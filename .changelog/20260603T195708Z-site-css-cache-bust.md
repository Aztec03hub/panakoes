---
category: Fixed
---

- `panakoes_site`: stylesheet link now carries a cache-busting version query; /assets/* is cached immutable for a year, so returning visitors were stuck on stale CSS after the wordmark change.

---
category: Fixed
---

- `ci`: throttle auto-recover-pr flood by using a single global concurrency group instead of per-SHA groups. Prevents hundreds of 1s runner slots being consumed on every push.

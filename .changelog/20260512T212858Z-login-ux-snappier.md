---
category: Changed
---

- `services/admin`: login form now shows a spinning indicator plus a "first sign-in of the day can take up to 15 seconds" hint while the auth service warms up, disables the inputs during the in-flight request, surfaces a clean timeout message after 30 seconds, and logs click-to-resolve duration to `console.debug` for latency triage.

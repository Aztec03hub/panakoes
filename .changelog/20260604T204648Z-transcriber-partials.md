---
category: Added
---

- `services/transcriber-stream`: live partial hypotheses now stream to the client (`{"type":"partial"}`, rate-limited to 2Hz) and every inference pass logs commit count + hypothesis preview; the container previously emitted neither, leaving users and operators blind to what the model hears.

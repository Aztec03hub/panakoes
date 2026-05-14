---
category: Added
---

- `infra/dev/service-discovery`: provision AWS Cloud Map private DNS namespace `panakoes-dev.local` for ECS Service Connect (W1-T1). Services will register as `<name>.panakoes-dev.local:<port>` once W1-T4 enrolls them.

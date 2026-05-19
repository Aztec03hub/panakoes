---
category: Added
---

- `infra/dev/backup`: provision parallel vault, plan, selection, and notifications under the consolidated `alias/panakoes/app-data` CMK from `infra/dev/kms/` (W2-T3). The legacy vault `panakoes-dev` and its per-vault CMK keep running unchanged; the new `panakoes-dev-consolidated` vault runs alongside it during the cutover window. README documents the 30-day / 365-day cutover sequence and the eventual retirement of the legacy vault in a follow-up PR.

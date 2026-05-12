---
category: Changed
---

- `docs/`, `CLAUDE.md`: repository path references updated from `/mnt/c/...` to `~/projects/panakoes` for WSL2 native ext4 filesystem location. Eliminates DrvFs rename/EACCES bugs and dramatically improves install / file-watch performance. The `panakoes-hardware` reference in CLAUDE.md stays as-is (separate Karl Long repo, not in this migration).

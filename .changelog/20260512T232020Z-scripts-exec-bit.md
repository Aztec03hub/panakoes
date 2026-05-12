---
category: Fixed
---

- `scripts/*.sh`, `scripts/*.py`, `.githooks/pre-push`: set executable bit (mode 100755) so scripts run on clean clones to Linux-native filesystems. Previously these were mode 100644 in git and only worked on Windows-mounted WSL `/mnt/c` (where DrvFs treats all files as 0777). After the WSL2 native-fs migration to ext4, the missing exec bits surfaced as `Permission denied` on every `make` target. Affects 17 files.

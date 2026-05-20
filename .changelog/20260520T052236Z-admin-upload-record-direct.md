---
category: Added
---

- `services/admin/upload`: record audio directly from the browser microphone via a new Upload/Record tab switcher. The Record tab captures WebM/Opus through MediaRecorder, shows a live elapsed timer + playback control on stop, then submits through the same pre-signed S3 PUT path as a file upload. Existing upload behavior is unchanged.

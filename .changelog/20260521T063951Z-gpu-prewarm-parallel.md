---
category: Fixed
---

- `services/gpu-spawner`: pre-warm now uses 8 parallel `dd` readers each covering a 400 MB slice of the 3 GB Whisper model.bin. The serial dd from the prior fix saw the same ~10 MB/s EBS-lazy-load latency cap as the original mmap hang (the limit is per-S3-fetch latency, not bandwidth), so 8 concurrent readers drop the cold-start pre-warm wall-clock from ~9 min to ~1-2 min.

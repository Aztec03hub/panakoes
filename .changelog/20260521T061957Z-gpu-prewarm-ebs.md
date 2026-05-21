---
category: Fixed
---

- `services/gpu-spawner`: UserData now pre-warms `/opt/whisper/models/large-v2-ct2/model.bin` via a background `dd` before `docker run`. The root EBS volume on a freshly-spawned EC2 is lazy-loaded from the AMI snapshot in S3 on first touch (~10 MB/s for the 3 GB model.bin = 5-8 min cold), which made the container hang at "Loading Whisper large-v2 model..." for the entire smoke deadline. The warmup runs in parallel with the docker pull, then `wait` joins it before launch so the container's `WhisperModel(...)` call hits a hot page cache and returns in the ~30-60 s GPU init the design budgets for.

---
category: Fixed
---

- `services/gpu-spawner`: switch GPU EC2 launches to on-demand. Spot capacity for g4dn.xlarge is regionally constrained (all 3 panakoes AZs went dry simultaneously on 2026-05-21 evening), so single-instance-per-session Spot is unreliable for a real-time UX. On-demand g4dn.xlarge in us-east-1 is ~$0.526/hr, ~3.3x the Spot price (~$0.16/hr). Short-running per-session instances (typical 1-5 min total) mean the wall-clock cost difference is pennies per session. A future PR can layer Spot-first-with-on-demand-fallback once cluster capacity recovers; this PR prioritizes availability over price.

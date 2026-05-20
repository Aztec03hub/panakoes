---
category: Changed
---

- `CLAUDE.md`: add a NON-SKIPPABLE end-to-end smoke gate before the orchestrator can claim any feature is "live," "shipped," "demo-ready," or similar. Lists the exact trap (declaring complete on review-clean + build-clean without running real traffic through the deployed pipeline) and the contract for the orchestrator (identify entry-point + canonical output, execute against the live deployment with a real fixture, read logs across every handoff, capture the smoke-passed paragraph in the run report). Drove by the streaming-transcription arc's bug cascade on 2026-05-20 where five adversarial review rounds + build + apply + deploy all came up clean but the first user click revealed eight one-line breakages that an end-to-end smoke would have caught.

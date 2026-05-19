---
category: Added
---

- `WORKFLOW.md` section 5.6 + `docs/templates/agent-brief-architect-reviewer.md` + `docs/templates/agent-brief-adversarial-reviewer.md`: canonicalizes Phil's two-stage Design Review Cycle. Stage 1 dispatches an architect-reviewer agent (positive / additive: improvements, must-adds, web-research findings) producing a structured markdown worklog; Phil reviews + picks; orchestrator updates the design. Stage 3 dispatches an adversarial-reviewer agent (negative / risk-finding: bugs, oversights, hidden assumptions categorized CRITICAL/HIGH/MEDIUM/LOW); Phil reviews + picks; orchestrator updates the design. The two agents have orthogonal mandates so they don't waffle. Easy-kickoff `scripts/design-review.sh` wrapper is a future-session TODO; until then, the manual sequence in section 5.6 is the canonical path. Triggered by Phil's request 2026-05-18.

---
category: Changed
---

- `ARCH-MIGRATION.md`: refresh section 1.6 active modules (add `alb`, `kms`, `service-discovery`; note `vpc-endpoints`, `waf`, `cloudfront-waf`, `auth-db` removed); rewrite section 1.7 Cost Profile with verified live numbers (~$109/mo gross post Container Insights cut, was ~$280 estimated); add a "Wave 1.5" mini-section between Wave 1 and Wave 2 capturing the Container Insights disable (PR #363) and the 4 failed-service scale-down; mark Wave 2 T1 as DISPATCHED in the Wave 2 task table with the design-decision notes from the agent's run report. Brings the doc back in sync with live AWS state as of 2026-05-18 and documents the cost trajectory clearly.

---
category: Added
---

- `.claude/agents/dependency-updater.md`: new project-scoped agent definition that handles dependency updates across all four ecosystems (Python via uv, TypeScript via pnpm, Terraform providers, GitHub Actions). The agent audits drift, categorizes bumps by risk (patch / minor / major / security / cross-cutting per ADR-029), reads migration guides for majors via WebFetch, applies mechanical refactors, runs local-first verification (`make ci-fast` plus the affected service's tests plus terraform validate) before pushing, and opens grouped PRs that preempt the Dependabot backlog. `CLAUDE.md` gets a new "Project Agents" section documenting the convention so future Claude sessions auto-discover the agent.

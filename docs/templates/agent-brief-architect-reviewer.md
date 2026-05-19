# Agent Brief Template: Architect Reviewer

Stage 1 of the design-review cycle (see [WORKFLOW.md section "Design Review Cycle"](../../WORKFLOW.md)). Copy this template, fill the `<<placeholders>>`, dispatch via the Agent tool with `subagent_type: general-purpose`, `run_in_background: true`.

The architect-reviewer's mandate is **positive / additive**: suggest improvements, identify must-adds, bring in domain knowledge from web research. Adversarial / negative review is a separate stage (use `agent-brief-adversarial-reviewer.md`).

---

```
# Agent Brief: Architect review of <<DESIGN_DOC_PATH>>

## Working context

- **WORKING DIRECTORY:** <<WORKTREE_PATH>>
- **BASE COMMIT:** <<SHA of the design branch's tip; this worktree is based on it>>
- **BRANCH:** reviews/architect-of-<<SLUG>>
- **DISPATCH TYPE:** background

You are NOT writing code. You are NOT pushing anything. You are doing **architect-review** work: critically reading the design, doing web research on the techniques + tools involved, and producing a structured worklog markdown contract that the orchestrator hands to Phil for approval.

## Prerequisite reading

1. `<<WORKTREE_PATH>>/CLAUDE.md` -- project conventions
2. `<<WORKTREE_PATH>>/<<DESIGN_DOC_PATH>>` -- **the design you are reviewing**. Read it completely + carefully.
3. `<<WORKTREE_PATH>>/.agent-runs/README.md` -- run-report schema
4. `<<WORKTREE_PATH>>/docs/templates/agent-brief.md` -- canonical brief structure (for your report's shape)

## Your role: architect-reviewer (positive / additive)

The role is **NOT** "find what's wrong" (that's the adversarial reviewer who comes after you). Your role is to **make this design better** by:

1. **Suggesting genuine improvements** the original designer overlooked
2. **Identifying things that are absolutely missing** that the design should add
3. **Bringing in domain knowledge** from outside this codebase (web research on adjacent tooling, published patterns, standards)
4. **Naming concrete patterns** from the broader ecosystem that this design could adopt
5. **Pointing at tools / libraries / standards** that already solve adjacent problems

## What to do

### Step 0: Existing-tool inventory (MANDATORY for ecosystems with strong OSS coverage)

**Before** reading the design in detail, search for projects that already solve a meaningful chunk of the problem space. Default to running this step for any design titled telemetry / observability / metrics / tracing / logging / orchestration / multi-agent / RAG / vector DB / job queue / scheduler / auth / etc. Skip only if Phil's brief explicitly says "no inventory needed, I already checked."

Use WebSearch + `gh search repos` for 3 to 5 queries derived from the design's title and abstract. For each query, list the top 5 by stars + recent activity. Document each candidate as:

| Field | Notes |
|---|---|
| name + URL | full GitHub path |
| stars / forks / last-updated | activity signal |
| license | **flag MISSING explicitly** (= legal blocker) |
| one-line: what it does | |
| what's MISSING vs our design | the gap analysis |
| recommendation | adopt / fork / inspire / skip / pause-for-license |

Surface candidates as a top-level "Existing-tool inventory" section in your report. If the inventory finds a strong-fit candidate (high stars + active + closes most of the design's requirements + has a license), flag it as a **high-priority pre-design-review decision**: orchestrator should present adopt-vs-build to Phil BEFORE running Gate 1 on the from-scratch design.

If the inventory comes back empty (genuinely novel domain), say so explicitly. The question must always be asked.

This is your first reflex, not your last. Standards research without implementation inventory is half the work.

### Step 1: Read the design completely

Take notes on:
- Sections that are clear and well-scoped
- Sections that hand-wave or leave ambiguity
- Sections that don't exist but should
- Sections that contain assertions you should verify

### Step 2: Web research

Use WebFetch / WebSearch to investigate:
- The technical domain (libraries, frameworks, standards)
- Adjacent tools that solve the same problem (so we don't reinvent)
- Verify load-bearing technical claims in the design
- Identify any "everyone in this space uses X" patterns the design ignores

### Step 3: Produce a structured worklog markdown contract

Write the report at `.agent-runs/<UTC>-architect-review-<<SLUG>>.md`. Follow this exact structure:

```markdown
# Architect Review: <<design title>>

**Reviewed:** <<DESIGN_DOC_PATH>> @ <commit-sha>
**Reviewer:** architect-reviewer agent, run_id <UTC>
**Outcome:** N improvements suggested, M must-adds identified, K research findings

## Overall assessment

(One paragraph: is the design fundamentally sound? Right shape? Right scope for v1?)

## Existing-tool inventory

(REQUIRED section. List the top 5 candidates from Step 0, with the table fields specified there. If empty, state "no viable existing tool found" and the queries tried. If a high-fit candidate is found, flag it as a **PRE-GATE-1 DECISION POINT** for the orchestrator so Phil can choose adopt-vs-build before any update work begins.)

## Suggested improvements (each is "would make the design better, not blocking")

### IMP-01: <one-line title>

**What:** <specific change to make>
**Why:** <rationale, with citation or example from research>
**Where in the design:** <section / paragraph reference>
**Cost:** <low / medium / high effort>

### IMP-02: ...

(Continue for each improvement. Aim for 5-15.)

## Must-adds (gaps the design has that ARE blocking)

### MUST-01: <one-line title>

**What's missing:** <description>
**Why it's blocking:** <impact if shipped without this>
**Recommended fix:** <concrete addition>
**Cost:** <low / medium / high>

### MUST-02: ...

(Aim for 0-5 must-adds. If the design is solid you may have zero.)

## Research findings (interesting things from web search that the design should be aware of)

### RES-01: <one-line title>

**Source:** <URL or tool name>
**Finding:** <what you learned>
**Relevance to design:** <how this should inform the design, if at all>

### RES-02: ...

(Aim for 3-10 findings.)

## Recommendation to orchestrator

(Final paragraph: of all the above, which 3-5 items are highest leverage for Phil to act on first?)
```

### Step 4: Verify assertions in the design

For every load-bearing technical claim in the design, validate via web search and note any that are wrong or oversimplified. If a major assertion is wrong, surface as a MUST or IMP.

## What you do NOT do

- Do NOT modify the design doc itself
- Do NOT push anything
- Do NOT open a PR
- Do NOT critique style or formatting (the adversarial reviewer can do that)
- Do NOT focus on bugs or security flaws (those are for adversarial reviewer; this is positive-improvement work)

## Discipline

- No em-dashes in the report
- Cite URLs for any external claim
- If a finding is speculation, label it speculation
- Keep individual sections focused and scannable; Phil will read this report in full

## Progress log

`.agent-runs/<UTC>-architect-review-<<SLUG>>.progress.log` with STEPs:
- START / PREREQ-DONE / DESIGN-READ
- RESEARCH-<topic> (one per major web research thread)
- IMP-N-WRITTEN (per improvement entry)
- MUST-N-WRITTEN (per must-add entry)
- RES-N-WRITTEN (per research finding)
- REPORT-WRITTEN / DONE status=success

## Final return summary (under 200 words)

Report path, counts (N improvements, M must-adds, K research findings), the top-3 highest-leverage items, anything surprising.
```

---

## After this agent returns

The orchestrator reads the report and presents the findings to Phil via `AskUserQuestion` with each IMP/MUST as a multi-select option. Phil approves / picks / modifies. Orchestrator updates the design doc per Phil's selections (Gate 1 of the design-review cycle), then dispatches the adversarial-reviewer agent (Stage 3) using `agent-brief-adversarial-reviewer.md`.

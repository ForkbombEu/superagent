---
name: write-issues
description: >
  Split a specification or plan into GitHub issues for a Superagent target
  repository. Use when opening a backlog, writing issues from a spec, sizing
  Effort, wiring Depends on, or drafting User Acceptance Tests.
---

# Write issues

Turn a specification (or feature plan) into GitHub issues that fit the
Superagent pipeline and the token budget. The repository's `AGENTS.md`
"Writing issues" section is the source of truth for the body shape; this skill
is the procedure that produces compliant issues.

## 1. Load the contract

Read `AGENTS.md` "Writing issues" (and its issue template) and the
specification or plan the person named (`docs/specification.md` unless they
point elsewhere).

Done when the hard rules are in context: agent `@` mention in the created
description, one pull request per issue, acceptance checks, `Depends on`,
optional UAT heading written exactly.

## 2. Split

Break the work into **slices** — each issue is one pull request's worth: one
module or feature slice, reviewable on its own.

- Split when two outcomes can be accepted separately; keep a producer and its
  consumer in one issue when either alone would be an invalid contract.
- Fit the smart zone: the files under **Context** plus the change and its
  tests. Estimate about 12 tokens per line read. More than ~40k tokens of
  context, or more than one subsystem → split further.
- Mid-tier models: Effort up to ~150k. High-tier design issues: up to ~200k,
  and the design issue lands a short ADR before implementation issues start.
- The last issue of a feature restores consistency: prompts, README,
  `.env.example` and tests agree with the code.
- Escalate **Model** only on an observable trigger (tests cannot express the
  contract, two failed attempts, a trust boundary).

Order slices so every prerequisite comes before its dependents; there must be
no cycles. On a new repository, the first issue sets up `mise.toml`, the
build, the test suite and a CI workflow whose checks are required on the
default branch.

Done when each slice has an imperative title, a one-paragraph ask, and a
place in the dependency order.

## 3. Draft each body

Write one body per slice, matching the `AGENTS.md` template and these Gestalt
L2 fields:

```markdown
@forkboteu <one paragraph: what to build and why, referring to
docs/specification.md>

- Effort :: ~100k
- Model :: mid
- Context :: path/a, path/b
- Branch after :: consistent

## Acceptance criteria
- [ ] <verifiable behavior>
- [ ] <tests that cover it>

## User Acceptance Tests
Environment: local

### UAT-1: <observable behavior>
Given <starting state>
When <what a user or client does>
Then <what they observe>

Depends on #<a>, #<b>
```

| Field | Rule |
| ----- | ---- |
| Effort | Implementer budget including tests and fixes (`~60k`…`~200k`). Derive from Context size (≈12 tokens/line) plus the change and tests. |
| Model | `low`, `mid` or `high` for the implementer tier. |
| Context | Paths the implementer must load; keep the list inside the smart zone. |
| Branch after | `consistent` when the feature branch works after this issue; otherwise `inconsistent` naming what is still missing. |
| Depends on | One line, `Depends on #<a>, #<b>`, naming only issues whose merged code this one needs. Omit the line when there are none. |
| UAT | Heading exactly `## User Acceptance Tests`. Each scenario is `### UAT-<n>: <title>` with non-empty steps (Given/When/Then recommended). Omit the whole section when no end-to-end check applies. A near-miss heading blocks the pull request. |

Never ask the agent to wait, hold, skip merge or seek approval.

Done when every draft has the mention, the four L2 lines, acceptance checks, a
valid `Depends on` line or none, and either a parse-ready UAT section or no
UAT heading.

## 4. Confirm, then open

Show the person the backlog: title, Effort, Model, Depends on, UAT yes/no.
Iterate until they approve the split.

Create the issues with that person's `gh` (never the agent account), in
dependency order so each `Depends on` names issues that already exist:

```bash
gh issue create --repo <owner>/<repo> --title "<title>" --body-file <file>
```

Open them all in that order: independent issues run in parallel; the rest
start as their prerequisites merge.

Done when every approved issue exists on the tracker and every `Depends on`
line resolves.

## Fixture check

`fixtures/` holds a sample specification and the issue bodies a dry run of
this skill produces from it. From the repository root:

```bash
python templates/.agents/skills/write-issues/check-fixtures.py
```

The script applies the same template checks (`@` mention, L2 fields,
acceptance criteria, `Depends on`, UAT parse via `uat.spec`). A dry run is
done when that command exits 0.

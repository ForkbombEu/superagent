# Wiki

Dated notes that outlive agent sessions: decisions, recurring problems,
preferences. A historical record, not a description of the code today: every
claim carries a date, and claims about code cite a commit. Superseded claims
are marked, not rewritten. Sources (PRs, issues, transcripts) are cited, never
copied.

## Current path

This wiki lives on `experiment/superagent-delivery` (formerly `attesta`):
delivery checks, `superagent.yaml` / role presets, and `superagent init`.
Claude chat in DSH is the sibling experiment `experiment/claude-plugin`.
Closed dead ends: draft Opus `/console` (#22) and third-party
`@norman-else/dsh-claude` (#40).

## Process

- [Branches and deploys](branches-and-deploys.md): rename from `attesta`, Coolify retarget, worktree layout.
- [User preferences](user-preferences.md): how Giovanni wants work done.
- [Subagent workspace scope](subagent-workspace-scope.md): subagents' file tools only reach the chat's own worktree.
- [Subagent context pressure](subagent-context-pressure.md): interrupt
  thrashing subagents; hand off sparsely to a fresh one.
- [Implementer git hygiene](implementer-git-hygiene.md): always
  `git status` before/after commit; stay on named feature branch; never
  stage npm-pack unpack dirs (gitignore them).

# Subagent workspace scope

- On 2026-11-20, a background implementation subagent started in another
  worktree could not write files there: Cursor's file tools (Read, Write,
  StrReplace, Grep) are scoped to the parent chat's workspace and refuse
  other worktrees of the same repository. Shell with full permissions still
  works, but writing code through shell is a workaround.
- What to do: check the feature branch out in the chat's own workspace before
  dispatching an implementer, or use a subagent type that gets its own worktree
  (`best-of-n-runner`, or a cloud agent).

Related: [Branches and deploys](branches-and-deploys.md).

# Implementer git hygiene

- On 2026-11-21, Phase 2 implementers on `feat/claude-plugin` repeatedly
  left the worktree in detached HEAD after commits, and once staged an
  npm-pack unpack directory (`.tmp-dsh-inspect`). Standing instruction:
  always `git status` before and after commit; stay on the named feature
  branch; never stage npm-pack unpack dirs; add such dirs to `.gitignore`.

Related: [Branches and deploys](branches-and-deploys.md),
[Subagent workspace scope](subagent-workspace-scope.md),
[User preferences](user-preferences.md).

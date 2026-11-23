# Branches and deploys

- As of 2026-11-20, Coolify deploys the Compose stack from `attesta` and
  redeploys on every push to it (confirmed with F0, `dd6d77b`). A person
  promotes `attesta` to `main`.
- Branch model D1 (`docs/feature-release-plan.md` at `ea564e5`): feature branch
  `feat/f<N>-<slug>` from `attesta`, issue branches `f<N>/<slug>` squashed into
  it, the feature squashed into `attesta`.
- Worktrees live under `~/.cursor/worktrees/superagent/<name>`; shell writes
  there and `gh` need unsandboxed permissions (handoff, 2026-11-20). File tools
  only reach the chat's own worktree ([Subagent workspace scope](subagent-workspace-scope.md)).
- CI (`.github/workflows/test.yml` at `ea564e5`): `unit`, `docker`, `build`.

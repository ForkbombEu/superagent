# Branches and deploys

- As of 2026-11-20, Coolify deployed the Compose stack from `attesta` and
  redeployed on every push to it (confirmed with F0, `dd6d77b`). A person
  promotes a chosen tip to `main`.
- On 2026-11-23, `attesta` was renamed to `experiment/superagent-delivery`.
  Retarget Coolify to that branch (or to `main`) so deploys keep working.
- Branch model D1 (`docs/feature-release-plan.md` at `ea564e5`): feature branch
  `feat/f<N>-<slug>` from the backbone, issue branches `f<N>/<slug>` squashed
  into it, the feature squashed into the backbone. Historical plan text still
  says `attesta` for that backbone.
- Worktrees live under `~/.cursor/worktrees/superagent/<name>`; shell writes
  there and `gh` need unsandboxed permissions (handoff, 2026-11-20). File tools
  only reach the chat's own worktree ([Subagent workspace scope](subagent-workspace-scope.md)).
- CI (`.github/workflows/test.yml`): `unit`, `docker`, `build` on
  `experiment/superagent-delivery` and `main`.

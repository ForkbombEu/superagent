# Agent notes — `experiment/superagent-delivery`

This checkout is the **`experiment/superagent-delivery`** branch of
[ForkbombEu/superagent](https://github.com/ForkbombEu/superagent) (formerly
`attesta`). It is an **experiment branch**, not `main`.

## What this branch is

The landed slice of [`docs/feature-release-plan.md`](docs/feature-release-plan.md)
(F0–F6, F9, F11; F7/F8/F10 skipped):

- **Delivery checks** — shared exit-code contract; `superagent check` (next step
  for a PR head) and `superagent merge` (every merge condition in code).
- **Roles / config** — tiered worker presets, Claude reviewer routing by diff
  size, deployment shape from [`superagent.yaml`](superagent.yaml).
- **Target-repo packaging** — planning skills under `templates/`, plus
  `superagent init` / `superagent upgrade`.
- **Process wiki** — standing prefs and worktree rules under `docs/wiki/`.

Related experiment (Claude chat in DSH): `experiment/claude-plugin`. Closed
dead ends: Opus `/console` (#22), `@norman-else/dsh-claude` (#40).

## How to treat the branch

- Do not merge to `main` unless a human explicitly asks.
- Coolify previously tracked `attesta`; after the rename, retarget the app to
  this branch (or to `main`) before expecting a live deploy.
- Conventional Commits; dense README; least new surface.
- CI runs on pushes to this branch and `main` (`.github/workflows/test.yml`).

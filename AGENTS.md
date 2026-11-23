# Agent notes — `experiment/claude-plugin`

This checkout is the **`experiment/claude-plugin`** branch of
[ForkbombEu/superagent](https://github.com/ForkbombEu/superagent). It is **not**
the deployed tip. Coolify deploys **`attesta`**.

## What this branch is

An experiment to ship Claude Code chat **inside normal DSH** via an owned thin
plugin (`dsh/claude/`, package name `superagent-claude`), instead of:

- third-party `@norman-else/dsh-claude` (closed PR #40), or
- the custom Opus Vite `/console` experiment (closed draft PR #22).

Product work order and phase evidence live under `docs/claude-plugin.md` and
`docs/claude-plugin-phase*.md`. Operator-facing install notes for the plugin are
in the README section **Claude in DSH (`superagent-claude`)**.

## How to treat the branch

- Do not merge to `attesta` or `main` unless a human explicitly asks.
- Prefer least new surface; keep credentials out of the DSH process (secrets
  file / Claude child `env` only).
- Conventional Commits; keep the README dense.
- Standing process notes for the repo (prefs, worktrees, subagents) are on
  `attesta` under `docs/wiki/`.

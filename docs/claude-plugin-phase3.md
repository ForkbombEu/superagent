# Phase 3 notes (`superagent-claude`)

Branch `feat/claude-plugin`. Cut over: no PATH shadow, credential unset kept,
promote-only primary workspace, README permission note. Secrets never printed.

## Done criteria

| Criterion | Result |
|---|---|
| Remove `claude` → `claude-agent` PATH shadow | **PASS** — npm `claude` on PATH; `scripts/claude-agent` deleted |
| Entrypoint unsets `CLAUDE_CODE_OAUTH_TOKEN` + `ANTHROPIC_API_KEY` | **PASS** — `unset ANTHROPIC_API_KEY CLAUDE_CODE_OAUTH_TOKEN` after secrets file |
| Primary workspace promote-only (no silent `/tmp` delete) | **PASS** — added `dsh/primary-workspace` without `registry.delete` |
| README documents plugin / permissions / credentials | **PASS** — Claude in DSH (`superagent-claude`) section |
| Design table `permissionMode` is chat `default` | **PASS** — `docs/claude-plugin.md` |
| Smoke on image DSH pin `0.2.2-rc.5` | **PASS** — `dsh --version` / package `0.2.2-rc.5` |
| Credential isolation (`/proc/$DSH_PID/environ` empty) | **PASS** — `CRED_ISOLATION_OK pid=7` |
| Quick chat turn smoke | **PASS** — haiku `runTurn` → `PONG`, `finish.stop` |

## Changes

1. Dockerfile: drop `claude.real` / `claude-agent` symlink; install
   `superagent-primary-workspace`.
2. Delete `scripts/claude-agent` (chat Agent SDK + `claude-review` own their env).
3. `dsh/primary-workspace`: promote `$HOME/src/<repo>` only — never
   `registry.delete` for `/tmp` (unlike earlier `feat/dsh-claude` draft).
4. README + work-order Design/Permissions wording for Phase 2 chat `default`.

## Smoke evidence (redacted)

- DSH: `0.2.2-rc.5`; `/usr/local/bin/claude` → npm `@anthropic-ai/claude-code`
  (`SHADOW_GONE_OK`, `NPM_CLAUDE_OK`).
- Creds: `CRED_ISOLATION_OK pid=7`; child `childHasCred: true`,
  `parentLeak: false`, managed `/etc/claude-code-chat`.
- Chat: `textPreview: "PONG"`, `finish: [{kind:"stop"}]`.
- Primary: `PRIMARY_PLUGIN_OK`, `NO_TMP_DELETE_OK`; unit test keeps `/tmp`
  registration while promoting checkout.

## Blockers / follow-ups for PR to `attesta`

1. **Existing home volumes** seed plugins only on first create. After deploy,
   once: `dsh plugin --profile superagent add 'link:/opt/superagent-primary-workspace'`
   (same class of migration as adding `superagent-claude`). Fresh empty volumes
   get it from the image build.
2. Phase 4 (worker → Claude delegation) remains out of scope.
3. Do not push from this agent; open/retarget PR to `attesta` when ready.

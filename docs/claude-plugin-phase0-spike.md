# Phase 0 spike notes (`superagent-claude`)

Observed on local compose (`claude-plugin-superagent-1`), branch `feat/claude-plugin`,
image with commits `5899338` + `ba0d6f8`. Drive path: `docker exec` →
`/opt/superagent-claude` `runTurn()` (no browser). Secrets never printed.

## Versions

| Component | Version |
|---|---|
| DSH | `0.2.2-rc.5` |
| Claude Code CLI | `2.1.318` |
| `@anthropic-ai/claude-agent-sdk` | `0.3.318` (pinned + resolved) |

## Fact 1 — preset without norman-else — **PASS**

- `dsh --profile superagent --dump-config`: `preset-superagent-claude` /
  name **Claude (Superagent)** / provider `superagent-claude`.
- `grep norman-else` on dump-config: **0** hits.
- `dsh plugin --profile superagent list`: `superagent-claude@link:/opt/superagent-claude`
  only (plus `dsh-superagent`); no `@norman-else/dsh-claude`.

## Fact 2 — auth with empty DSH parent Claude env — **PASS** (2026-11-21 re-prove)

Parent check (real PID, not self-matching `pgrep`):

```text
DSH_PID=7  # node /usr/local/bin/dsh --profile superagent …
cat /proc/7/environ | tr '\0' '\n' | grep -E 'CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY'
→ (no matches)
```

Entrypoint already `unset`s those after writing `/run/superagent-secrets/claude-review.json`
(`scripts/superagent-entrypoint.sh`). Compose still injects them into *new*
`docker exec` shells; smoke scrubbed them to match PID 7.

Stack refresh for re-prove: local `.env` gained `ANTHROPIC_API_KEY` (OAuth still present
but not refreshable); `docker compose -f compose.yaml -f compose.dev.yaml up -d
--force-recreate superagent` so entrypoint rewrote the secrets file.

Adapter path with scrubbed process env:

- `buildClaudeEnv()` → child received Claude credential(s) from the secrets file
  (`childHasCred: true`, managed settings `/etc/claude-code-chat`).
- SDK spawn reached Anthropic; assistant text returned (`finish: stop`).
- **Not** a missing-key / scrub failure. **HARD STOP for DSH spawn-scrub does not apply.**

Earlier same-day attempt (before API key): `401 OAuth access token has expired` —
credential overlay already worked; auth was the only blocker.

## Fact 3 — second-turn resume — **PASS** (2026-11-21 re-prove)

- Session store: `~/.dsh/state/superagent/claude-sessions.json`.
- Same agent id across two `runTurn()` calls kept the same Claude `session_id`
  (`sameSession: true`, id length 36).
- Turn 1: remember a random nonce → assistant text confirmed.
- Turn 2: ask for the nonce → reply matched (`containsNonce: true`).

## Blocker / next

1. ~~Rotate Claude OAuth (or API key)~~ — API key path proven for Phase 0 smoke.
2. ~~Re-run the two-turn `runTurn` smoke~~ — facts 2+3 green.
3. No design change; do not add parent-env credential workarounds.
4. Phase 1 polish can proceed after supervisor review of this spike.

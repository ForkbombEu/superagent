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

## Fact 2 — auth with empty DSH parent Claude env — **BLOCKED (not scrub)**

Parent check (real PID, not self-matching `pgrep`):

```text
DSH_PID=7  # node /usr/local/bin/dsh --profile superagent …
cat /proc/7/environ | tr '\0' '\n' | grep -E 'CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY'
→ (no matches)
```

Entrypoint already `unset`s those after writing `/run/superagent-secrets/claude-review.json`
(`scripts/superagent-entrypoint.sh`). Compose still injects them into *new*
`docker exec` shells; smoke scrubbed them to match PID 7.

Adapter path with scrubbed process env:

- `buildClaudeEnv()` → child received a Claude credential from the secrets file
  (`childHasCred: true`, managed settings `/etc/claude-code-chat`).
- SDK spawn reached Anthropic; **not** a missing-key / scrub failure.
- Result: `401 OAuth access token has expired. Re-authenticate to continue.`

**HARD STOP for DSH spawn-scrub does not apply.** Credential overlay reaches the
Claude child. Refresh `CLAUDE_CODE_OAUTH_TOKEN` (or set `ANTHROPIC_API_KEY`) in
compose/Coolify secrets and re-run the two-turn smoke.

## Fact 3 — second-turn resume — **NOT PROVEN** (blocked by fact 2)

- Session store written: `~/.dsh/state/superagent/claude-sessions.json`.
- Two failed turns for the same agent id kept the same Claude `session_id`
  (`sameSession: true`), but no successful recall of prior user content
  (both turns 401'd before assistant text).

Re-prove after a fresh credential: turn1 ask for a nonce token; turn2 ask what
the nonce was; require identical stored session id and matching reply.

## Blocker / next

1. Rotate Claude OAuth (or API key) in the local/Coolify secret source.
2. Re-run the two-turn `runTurn` smoke (or one UI session) for facts 2+3 green.
3. No design change; do not add parent-env credential workarounds.

# Phase 2 notes (`superagent-claude`)

Branch `feat/claude-plugin`. Drive path: unit tests locally + `docker exec` →
`/opt/superagent-claude` `runTurn()` with a mock `ctx.approval`. Secrets never
printed.

## Done criteria

| Criterion | Result |
|---|---|
| Approval bridge `ctx.approval` → `canUseTool` | **PASS** — unit + smoke |
| Chat `permissionMode: 'default'` | **PASS** — `turn.js`; unit asserts |
| Outside allow list → approval; both answers | **PASS** — curl Bash: reject → `CURL_BLOCKED` / deny; allow → `CURL_DONE 200` |
| MCP executes under chat (leave Phase 1 dontAsk) | **PASS** — `mcp__…__list_projects` `behavior:allow`, text `MCP_OK credimi-attestbook` |
| `AskUserQuestion` → `ctx.userQuestions` | **PASS** — unit (map + bridge); no interactive UI smoke |
| Plan mode | **PASS** (low-cost) — model `opusplan` → `permissionMode: 'plan'` |
| Empty-`errors` resume edge | **PASS** — unit |
| Credential isolation re-check | **PASS** — `/proc/$DSH_PID/environ` empty of Claude keys (`DSH_PID=7`) |
| `claude-review` stays `dontAsk` | **PASS** — script + review managed settings untouched |

## Security prerequisites

1. **`canUseTool` deny-by-default** — deny rules first; allow-list auto-allow;
   else `approval.request`; only `allowed-once` grants.
2. **Chat `default` / review `dontAsk`** — chat only; review script unchanged.
3. **MCP after allow-list + bridge** — MCP on chat allow list auto-allows in
   `canUseTool` (no dialog); smoke saw `behavior: allow`.
4. **Review MCP registration** — empty seed is **not** easy: CLI 2.1.318 loads
   enterprise `managed-mcp.json` only from `/etc/claude-code/`, which chat needs.
   Kept shared seed; review still cannot execute MCP (`dontAsk` + no `mcp__*`
   allow). Plugin also passes SDK `mcpServers` with `alwaysLoad: true`.
5. **Credential isolation** — re-proven after Phase 2 image recreate.
6. **README** — documents the enterprise MCP seed and that review denies execute.

## Smoke evidence (redacted)

- MCP: reasoning `mcp codebase-memory list_projects`; `mcpCanUse: allow`;
  `MCP_OK credimi-attestbook`.
- Approval reject: `approvalCalls: [{ toolName: Bash }]`, `behavior: deny`,
  `CURL_BLOCKED`.
- Approval allow: same ask → `behavior: allow`, `CURL_DONE 200`.
- Creds: `CRED_ISOLATION_OK pid=7`.

## Blockers / follow-ups

- Interactive DSH UI approval dialog not driven in this smoke (mock
  `approval.request` proves both outcomes on the bridge).
- Removing `/etc/claude-code/managed-mcp.json` breaks chat MCP with current CLI;
  revisit if Claude Code loads managed MCP from `CLAUDE_CODE_MANAGED_SETTINGS_PATH`.

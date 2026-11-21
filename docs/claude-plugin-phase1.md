# Phase 1 notes (`superagent-claude`)

Branch `feat/claude-plugin`, commits through Phase 1 MVP. Drive path: unit tests
locally + `docker exec` → `/opt/superagent-claude` `runTurn()` for smoke.
Secrets never printed.

## Done criteria

| Criterion | Result |
|---|---|
| Streaming text from partial messages | **PASS** — unit test; `text-delta` before `block-end` |
| Activity lines (tool/mcp/agent) in `reasoning` | **PASS** — unit + smoke |
| Models / effort / cancel / resume | **PASS** — adapter models; abort smoke `finish.kind=aborted`; resume unit + Phase 0 |
| Unit tests (`credentials`, `sessions`, mapping) | **PASS** — 15 tests (`test_claude_plugin` + `test_claude_settings_split`) |
| Fact 1 New Session UI | **PASS** — localhost:18080 shows preset **Claude (Superagent)** (also dump-config) |
| MCP activity + answer naming caller file | **PASS** (see symbol substitute) |
| Agent subagent turn | **PASS** — reasoning `Agent: Find selectLatestVersion definition` |
| Stop cancels running turn | **PASS** — abort → `finish.kind=aborted` |

## Symbol substitute

`checkoutPath` / `dsh/primary-workspace/index.js` **do not exist** on this branch
(or on `attesta`). Smoke used the live checkout `credimi-attestbook`:

- Symbol: `selectLatestVersion`
- Defined: `ui/src/catalog.ts`
- Callers named in the answer: `ui/src/catalog.ts` (`buildCatalogEntry`), `ui/src/detail.ts`

## MCP under `dontAsk`

Claude Code `2.1.318` registers enterprise MCP from
`/etc/claude-code/managed-mcp.json` (not from
`CLAUDE_CODE_MANAGED_SETTINGS_PATH`). Dockerfile seeds the chat MCP file there
as well as under `/etc/claude-code-chat/`.

Chat allow list includes `mcp__codebase-memory__*` and `Task`. Under
`permissionMode: 'dontAsk'`, MCP tool **calls** are still denied by the CLI
(“don't ask mode”) even when listed in `permissions.allow`; `default` +
`canUseTool` allow works. Phase 1 smoke therefore shows MCP **activity lines**
(`mcp codebase-memory search_graph`) then a correct answer (often via grep
fallback). Phase 2 approval bridge / `default` mode should make MCP execute.

Review keeps empty MCP allow rules and no `mcp__*` in `permissions.allow`, so
`dontAsk` still denies MCP tool use for `claude-review`.

## Smoke evidence (redacted)

- MCP turn reasoning included `mcp codebase-memory list_projects` /
  `search_graph`; answer named `ui/src/catalog.ts` and `ui/src/detail.ts`.
- Agent turn reasoning included `Agent: Find selectLatestVersion definition`.
- Cancel: `finish: { kind: 'aborted', failure: { code: 'aborted', … } }`.

## Remaining for Giovanni

- Optional: confirm one interactive UI turn (opus) if you want browser-level
  streaming/Stop beyond `runTurn` abort.
- Phase 2: approval bridge so MCP runs under allow list (leave `dontAsk` quirk).

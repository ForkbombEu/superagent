# Work order: `superagent-claude`, a thin Claude Code chat plugin for DSH

Replace `@norman-else/dsh-claude` with a plugin we own that does one thing: run
Claude Code as the agent of a DSH conversation, on the repository checkout, with
credentials only in the Claude process. Claude Code keeps its own agent loop,
tools, MCP servers and subagents; the plugin is the bridge.

## Why

- norman-else is ~450 KB of server code plus a ~790 KB client bundle (plan UI,
  Jira, PR actions, worktrees, bilingual chrome), built against DSH 0.1.7 while
  `attesta` runs DSH `0.2.2-rc.5`. We use none of that surface.
- It spawns Claude through DSH's subprocess service, which strips `*_TOKEN` /
  `*_KEY`. The image used to shadow `claude` with `scripts/claude-agent` and
  keep `CLAUDE_CODE_OAUTH_TOKEN` on the DSH process. Both are rejected in the
  PR #40 review; Phase 3 removes that shadow and keeps credentials only in the
  secrets file / child env.
- It sets `allowDangerouslySkipPermissions: true` and offers Bypass in the UI.

## The DSH contract (all public, verified against DSH `0.2.2-rc.5` sources and norman-else 0.1.67)

A Claude preset needs two plugin rows in `cordis.patch.yml`:

1. **Preset row** `@deepseek-ai/dsh-agent-preset` with `id`, `name`,
   `description`, and `plugins:` listing a **preset route** plugin. The route
   listens on `agent/request` and rewrites `provider` / `model` to our provider
   id. norman-else's route is 30 lines plus presentation-only tool cards.
2. **Provider row**: a host plugin injecting `llm` (plus `agents`,
   `agentPresets`) that calls `ctx.llm.registerAdapter([providerId], adapter)`.
   The adapter subclasses `LlmAdapter` from `@deepseek-ai/dsh-llm` and
   implements `stream(options)`, `listModels()`, `resolveModel()`,
   `providerInfo()`. Contract: `docs/subsystems/llm-streaming.md` in the DSH
   repo ("The adapter contract", "`StreamChunk`").

**One DSH step is one whole Claude Code turn.** The adapter yields only `text`
and `reasoning` blocks and ends with `finish {kind: 'stop'}`. It never yields
`tool-call` blocks, so the DSH loop never executes a tool; Claude Code ran them
inside its own process. This is how norman-else works (`ClaudeCodeAdapter.stream`).

Useful facts:

- Session working directory: `agent.session.header.cwd` (the primary-workspace
  plugin makes this `$HOME/src/<repo>`).
- Purposes: `options.purpose === 'session-title'` and other auxiliary calls
  reach the adapter too. Reject them in the MVP; DSH falls back to its default title.
- DSH upstream `@deepseek-ai/dsh-subagent-claude-code` already uses the auth
  pattern we want: scrub credential-shaped env, then layer an explicit `env`.
  Read its `src/run.ts` before writing the spawn code.

## Design

### Spawn and auth

Per turn, call the Agent SDK `query()` from `@anthropic-ai/claude-agent-sdk`
(pin one version; norman-else 0.1.67 uses `0.3.247`) with:

| Option | Value |
|---|---|
| `cwd` | `agent.session.header.cwd` |
| `pathToClaudeCodeExecutable` | the image's npm `claude` (one CLI version for chat and review) |
| `env` | scrubbed `process.env` + `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) read from `/run/superagent-secrets/claude-review.json` at turn time + `CLAUDE_CODE_MANAGED_SETTINGS_PATH=/etc/claude-code-chat` |
| `permissionMode` | `default` (managed allow list auto-allows; deny list denies; everything else asks via DSH `ctx.approval`) |
| `resume` | the Claude session id stored for this DSH agent |
| `model`, `effort` | from the request; aliases `opus`, `sonnet`, `haiku` |
| `includePartialMessages` | `true`, for streaming text |

Use the SDK's own spawn, not `ctx.subprocess`, so the explicit `env` reaches the
child. The DSH process never holds a Claude credential, and there is no global
`claude` shadow.

### Session continuity

Map DSH agent id to Claude `session_id` (from the SDK `system/init` message) in
a small JSON file under the DSH state dir, written atomically. The next turn
passes `resume`. A missing or failed resume starts fresh and says so in the turn
text. No process pool in the MVP: one `query()` per turn, aborted on DSH cancel.

### Rendering

- Assistant prose: `text` blocks, streamed from partial messages.
- Tool activity and subagent starts: one line each (`Read src/app.ts`,
  `Bash: npm test`, `Agent: explore auth flow`, `mcp codebase-memory search_graph`)
  in `reasoning` blocks, so they collapse in the UI.
- Usage: a `usage` chunk before `finish`.

No client bundle in the MVP.

### Permissions

Chat policy is `claude-code/managed-settings.chat.json` (Edit/Write/Agent and
the codebase-memory MCP allowed, Bypass disabled). Chat uses
`permissionMode: 'default'` with the DSH approval bridge (`ctx.approval` → SDK
`canUseTool`): allow-list tools auto-allow, deny-list tools deny, everything
else prompts. Review stays `dontAsk` with its locked managed settings.

## Files

```
dsh/claude/
  package.json        dsh.bundle.patch, dependency on the Agent SDK
  cordis.patch.yml    preset row + provider row
  index.js            provider plugin: adapter registration, listModels/resolveModel
  route.js            preset route: agent/request → our provider
  turn.js             one turn: build options, run query(), map SDK messages to StreamChunks
  credentials.js      read the secrets file, build the scrubbed env
  sessions.js         agent id → Claude session id store
uat/tests/test_claude_plugin.py          node-driven unit tests
uat/tests/test_primary_workspace.py      promote-only primary workspace
dsh/primary-workspace/                   host plugin: promote $HOME/src/<repo>
```

Dockerfile: copy `dsh/claude` to `/opt/superagent-claude`, `npm ci --omit=dev`
there, `dsh plugin --profile superagent add 'link:/opt/superagent-claude'`.

Use preset id `superagent-claude` and provider id `superagent-claude`. Remove
`@norman-else/dsh-claude` from the Dockerfile in the same change; the two do not
run side by side.

## Phases

Each phase ends when its criterion holds on a locally built image (`compose.yaml` + `compose.dev.yaml`).

### Phase 0: spike (prove the three risky facts)

1. With norman-else removed, preset **Claude (Superagent)** appears in New Session on DSH `0.2.2-rc.5`.
2. A turn authenticates while `cat /proc/$(pgrep -f 'dsh superagent')/environ | tr '\0' '\n' | grep -E 'CLAUDE_CODE_OAUTH_TOKEN|ANTHROPIC_API_KEY'` prints nothing.
3. A second turn recalls the first (resume works).

Done when all three are observed and noted in the PR. If (2) fails because DSH
scrubs the plugin's own spawn, stop and report: that changes the design.

### Phase 1: MVP

Streaming text, activity lines, models, effort, cancel, resume, unit tests for
`credentials.js`, `sessions.js` and SDK-message → chunk mapping.

Done when: in the UI, on preset Claude (Superagent) with model `opus`, one turn
asks "who calls `checkoutPath`?" and the activity shows a
`codebase-memory` MCP tool call and a final answer naming
`dsh/primary-workspace/index.js`; one turn uses an `Agent` subagent; Stop
cancels a running turn; unit tests pass.

### Phase 2: interaction

Approval bridge (`ctx.approval` → `canUseTool`) and switch the chat default to
`permissionMode: 'default'`; `AskUserQuestion` through `ctx.userQuestions`; plan
mode as a model choice (`opusplan`) or a preset config field.

Done when an action outside the allow list shows a DSH approval dialog and
both answers behave.

### Phase 3: cut over

- Remove the `claude` → `claude-agent` shadow; `claude-review` already passes
  credentials in its own `env`.
- Entrypoint unsets `CLAUDE_CODE_OAUTH_TOKEN` along with `ANTHROPIC_API_KEY`.
- `superagent-primary-workspace` stops deleting `/tmp` registrations (promote only).
- README: replace the Claude-in-DSH section with what the plugin does, the
  permission model (default mode, allow list, no Bypass), and where credentials live.

Done when the PR #40 review's four acceptance points hold: smoke on the image's
DSH pin, credential isolation, the permission note, no silent `/tmp` deletion.

### Phase 4: worker → Claude delegation

After cut-over, DeepSeek workers can spawn Claude as a nested subagent through
the existing F5/F6 `tool-subagent` allowlist (no prototype fork).

1. Add a Claude role in `superagent.yaml` whose preset id is `superagent-claude`,
   and include that id in the worker's `allowedAgentPresets` (rendered into
   `dsh/profile.patch.yml`).
2. Verify a spawned child inherits the ticket worktree cwd (same
   `agent.session.header.cwd` contract as interactive chat).
3. Per-preset permission config: a read-only planner preset vs an implementer
   preset (different managed allow lists / `permissionMode` as needed).
4. Delegated turns stay `dontAsk` forever (no human in the loop).

Done when a worker can list and spawn `superagent-claude`, the child runs in the
ticket worktree, and planner vs implementer presets differ in what they may
edit.

Risks (also under Risks below): Claude implementing and `claude-review`
reviewing the same change weakens reviewer independence; chat and review share
one OAuth quota.

## Out of scope

Plan review panel, PR/commit/merge actions, Jira, worktree management, prompt
suggestions, snippets, message steering, rewind, custom transcript renderer,
Agent Team roster, plugin self-update, i18n.

## Risks

- **DSH context accounting.** DSH sees one short exchange per turn while Claude
  holds the real context. Report a large context window in `resolveModel()` and
  reject `compaction` purposes so DSH never compacts a Claude session.
- **Agent SDK churn.** Pin the SDK; Phase 0 confirms it drives the image's CLI version.
- **Spawn scrubbing.** If DSH patches `child_process` for host plugins, Phase 0
  step 2 fails; fallback is `ctx.subprocess` with an explicit env overlay, as in
  `dsh-subagent-claude-code`.
- **Reviewer independence (Phase 4).** A Claude implementer plus `claude-review`
  on the same PR is the same model family reviewing its own work; prefer
  DeepSeek implement + Claude review, or a separate reviewer account/model, when
  independence matters.
- **Shared OAuth quota (Phase 4).** Interactive chat, delegated worker turns, and
  `claude-review` share `CLAUDE_CODE_OAUTH_TOKEN`; heavy delegation can starve
  reviews.

## Base branch

Branch from `attesta` (it pins DSH `0.2.2-rc.5`; Coolify deploys it). Carry over
the chat/review managed-settings split and codebase-memory MCP work from
`feat/dsh-claude`. PR #40 is superseded once Phase 3 lands.

# Extending superagent: agent roles, model routing, UI, packaging

Working notes for the `feat/agent-roles` branch: the ideas, what the
deployment does today, what the referenced projects offer (with sources), and
a proposed direction. Research done 2026-11-20 against
`deepseek-harness@2e39e70`, `dsh-superagent-prototype@8c32a9d` and the
projects' default branches on that date. Statements marked *Inference* are
judgments, not facts from a source. Sequenced delivery is in
[feature-release-plan.md](feature-release-plan.md).

## 1. Ideas

- Make the configuration visible and clearly configurable: agent roles
  defined in one YAML or code file.
- Better UI feedback and exploration of what agents are doing.
- Use native plugins, subagents and MCP wherever possible.
- Evaluate imperative agent execution (explicitly invoking the tester or the
  reviewer): a Mastra plugin, or something integrated with the DSH harness?
  Watch for responsibilities overlapping with DSH Superagent.
- Route models by task complexity: high tier for planning, mid tier for
  implementation, cheap models for small tasks.
- Make it something you install in your repository, or directly in your web
  app, the way Payload CMS and EmDash CMS install.
- UI: make multi-user behavior explicit, maybe even multiplayer.
- Nice to have: proper context management. Every agent stays within the
  "smart zone" of its context window.

Target roles, layered by area of responsibility and knowledge density; each
layer has its own instructions file (an `AGENTS.md`):

1. **Architect**: high-level view of how modules connect; writes code that
   reads like a DSL; knowledge from a code graph (code intelligence); high-tier
   model. Variant: strategic planner.
2. **Implementer**: mid-to-low-level view of how modules are implemented;
   idiomatic code for the stack, functional style, DRY; knowledge from text
   search, docs, MCP, research; mid-tier model. Variants: frontend, backend,
   devops (infrastructure as code), fixer.
3. **UAT tester / QA**: user perspective; knows what the software is supposed
   to do; reads very light specs: a short co-located README per module that
   also exports its glossary.
4. **Tester**.
5. **Code reviewer**: runs in parallel with QA.
6. **Bug hunter**.
7. **Task-effort evaluator**: keeps each issue within a ~150k-token budget,
   estimating token volume in and out per source.

## 2. Where agent configuration lives today

| Role                                         | Model                                                                                                                             | Instructions                                                  | Tools and permissions                                              | Invoked by                                                    |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------ | ------------------------------------------------------------- |
| Implementer (DSH Superagent worker)          | `SUPERAGENT_EXECUTOR_*` → `superagent-configure` → `settings/executor`                                                            | `prompts/delivery.md` (+ `delivery-uat.md`) → `PUT prompts`   | upstream `WORK_TOOLS`, not configurable here                       | DSH Superagent, one worker per ticket                         |
| Reviewer (Claude Code CLI)                   | `SUPERAGENT_REVIEWER_MODEL/EFFORT` → `/run/superagent-secrets/claude-review.json`                                                 | `claude-code/review-prompt.md`                                | `claude-code/managed-settings.json`                                | the worker runs `claude-review`, per the Delivery requirements |
| UAT tester (`dsh` headless)                  | `SUPERAGENT_UAT_PROVIDER/MODEL` → `uat/tester/profile.patch.yml`                                                                  | `prompts/uat-tester.md`                                       | `uat/tester/profile.patch.yml` (no web, subagents or workflows)    | the worker runs `uat-test`; `uat-gate` runs the tester        |
| Planner (whoever writes the backlog)         | the person's own agent                                                                                                            | `templates/AGENTS.md` "Writing issues"                        | the person's own `gh`                                              | a person, outside the deployment                              |

Observations:

- One role's configuration spans environment variables, a Markdown prompt, a
  JSON or YAML policy file and a script that applies it. A roles file has to
  generate all of these, so `superagent-configure` is the natural place to
  turn it into settings.
- Imperative execution already exists: the worker calls `claude-review` and
  `uat-test` as commands whose exit codes decide the next step. What needs
  work is how they are called and how their output is handled (section 5,
  "Better calls and outputs for the checks"), not a new orchestrator.
- Target repositories already carry a schema-checked config file,
  `.superagent/uat.yaml`. That is a precedent for `.superagent/` as the
  installable part.
- Trust boundary (from the existing UAT design): the pull request and the
  issue only choose within a policy the deployment owns. Anything that
  configures the reviewer or the tester (model, prompt, permissions) must stay
  deployment-owned or come from the protected default branch, never from the
  pull request being checked.

## 3. What DSH and DSH Superagent already offer

From [DSH creator mode and extension points](https://github.com/deepseek-ai/deepseek-harness/tree/2e39e70):

- **A role in DSH is a preset**: a YAML list of plugins (persona, tools, shell
  limits, skills, compaction, delegation)
  ([agent-preset](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70/packages/preset/agent-preset/README.md#L28-L48)).
  Superagent already ships two
  ([cordis.patch.yml](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/cordis.patch.yml#L20-L27)).
- **Creator mode** is the built-in preset `cordis`: an agent that customizes
  DSH itself, writes plugins and composes tools and prompts into new modes,
  with `plugin_manager`, `plugin_market`, `cordis_inspect` and the `dsh-doc`
  skill
  ([locales](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70/packages/client/ui-agent-preset/src/client/locales.ts#L41-L73),
  [preset](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70/packages/bundle/web-app/presets/cordis.patch.yml#L10-L17)).
  It authors DSH presets and plugins, not repository code: useful for
  prototyping role presets, not a role itself.
- **Subagents with per-call models**: the `subagent` tool takes
  `agentPreset`, `allowedAgentPresets` (the model picks the preset),
  `modelSelection` (the model passes `provider`, `model`,
  `reasoning_effort`, authorized against a route list) and `maxDepth`
  ([tool-subagent](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70/packages/subagent/tool-subagent/README.md#L45-L79)).
  Superagent workers already have `subagent` with `modelSelection: true` and
  `agentPreset: standard`
  ([cordis.patch.yml](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/cordis.patch.yml#L104-L111)),
  and `WORK_TOOLS` includes `subagent`, `list_subagent_models`,
  `send_message`, `list_agents` and `interrupt_agent`, but no workflow tool and
  not `subagent_claude_code`; a worker's subagents get the same restricted list
  ([sessions.ts](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/src/app/sessions.ts#L61-L62)).
- **Skills** are `SKILL.md` folders found in `.dsh/skills`, **`.agents/skills`**,
  custom dirs and `$DSH_HOME/skills`
  ([skill-filesystem](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70/packages/skill/skill-filesystem/README.md#L34-L56)):
  skills committed to a target repository reach its workers without any
  deployment change.
- **MCP**: an `@deepseek-ai/dsh-mcp-client` row per server (stdio or
  Streamable HTTP) in a profile patch; tools appear as `mcp__<server>__<tool>`
  ([guide](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70/docs/user/guide/mcp-memory.md#L11-L33)).
- **No routing by complexity or cost** exists in DSH; the model-selection UI
  says it is not a router
  ([ui-model-selection](https://github.com/deepseek-ai/deepseek-harness/blob/2e39e70/packages/client/ui-model-selection/README.md#L109)).
- **Superagent settings**: `policy`, `eval`, `executor`, `source:<id>`,
  `paused`; `eval.model` and `executor.model` are per-role models
  ([policy.ts](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/src/policy.ts#L70-L105)).
  Every worker gets the same preset `dsh-superagent-worker`, the same model
  and the same tools
  ([sessions.ts](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/src/app/sessions.ts#L138-L155)).
  Prompt overrides are per role (orchestrator or worker), not per ticket
  ([prompts.ts](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/src/app/prompts.ts#L102-L116)).
- **Read API**: `state`, `overview`, `tickets?state=&offset=&limit=`,
  `tickets/:id`, `threads`, `decisions`, `settings`, `prompts`, `models`
  ([api.ts](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/src/app/api.ts#L20-L45));
  only the routes in the deployment doc are stable, the rest "change with" the
  panel
  ([006](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/docs/006-deployment.md#L167)).
  There is no HTTP route for worker transcripts.
- **Version drift**: the prototype depends on dsh `^0.2.1-alpha.2`
  ([package.json](https://github.com/deepseek-ai/dsh-superagent-prototype/blob/8c32a9d/package.json#L55)),
  and upstream's deployment doc pins `0.2.1-rc.3`; this branch moves both
  images to `0.2.2-rc.5`, with which the plugin starts and the tester tests
  pass. Remaining risk: the plugin is built against 0.2.1-era packages
  (cordis 4.0.5; dsh now ships 4.1.0), and 0.2.2 needs Node 24.9 or newer
  (the images have 24.22). The tester profile drops the removed
  `tool-subagent-list-agents` row and disables the new `tool-agent-team`.

Without forking we can: add role presets to the `superagent` profile patch and
let workers spawn them through `allowedAgentPresets`; pick subagent models per
call; set executor and eval models; add team prompt text and
`dsh-superagent-*` skills; add MCP rows; ship skills in the target repository.

Needs upstream changes or a fork: a role, model or tool set per ticket; any
tool outside `WORK_TOOLS` (`subagent_claude_code`, a `subagent_<role>`
tool); a planning stage before implementers; code-enforced reviewer and tester
order; a stable read API for tickets and transcripts.

## 4. Referenced projects

| Project                                                           | Verdict                    | License                          |
| ----------------------------------------------------------------- | -------------------------- | -------------------------------- |
| [Gestalt](https://dyne.org/gestalt/)                              | borrow ideas               | MIT; `context-mode` is ELv2      |
| [mattpocock/skills](https://github.com/mattpocock/skills)         | adopt a subset             | MIT                              |
| [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | adopt for the architect, after a container test | MIT          |
| [Mastra](https://mastra.ai/)                                      | borrow ideas               | Apache 2.0 (except `ee/`)        |
| [Pi](https://github.com/earendil-works/pi)                        | pilot for read-mostly roles | MIT                             |
| [Wayfinder Atlas](https://bbtgnn.github.io/wayfinder-atlas/)      | reuse (our own project)    | ours to set                      |

**Gestalt** ([repo](https://github.com/dyne/gestalt-agents), v2.26.2) is a
methodology plus Codex plugins and skills, not an Emacs tool. A read-only root
agent plans, reviews and supervises one executor per milestone, with evidence
gates and a whole-branch review at the end
([README](https://github.com/dyne/gestalt-agents/blob/HEAD/README.md)). Plans
are org-mode outlines in `.gestalt/<topic>.org`: milestones and steps with
`TODO/WIP/DONE/SUSPENDED`, priorities and property drawers, changed only
through an `org-plan` state machine
([plan format](https://github.com/dyne/gestalt-agents/blob/HEAD/plugins/gestalt/skills/org-plan/references/plan-format.md)).
An optional per-milestone model policy sets `MODEL`, `REASONING_EFFORT`,
`MODEL_RATIONALE`, `MODEL_FALLBACKS` and `MODEL_ESCALATION_TRIGGER`
([schema](https://github.com/dyne/gestalt-agents/blob/HEAD/plugins/gestalt/skills/org-plan/references/model-policy-schema.md)).
Gestalt Mobile is a web relay showing plan progress, write approvals and
blockers that need a person ([site](https://dyne.org/gestalt/)).
*Inference*: Codex-only and single-writer, so it conflicts with parallel
tickets; borrow the per-task model policy fields, the evidence gates and the
"needs attention" signal.

**mattpocock/skills** (v1.3.2) packages `SKILL.md` folders as a Claude Code
plugin or via `npx skills add` for other agents
([README](https://github.com/mattpocock/skills#installation-30-second-setup)).
Model-invoked skills map onto roles: `codebase-design`, `domain-modeling`
(glossary and ADRs) and `improve-codebase-architecture` for the architect;
`tdd` for implementers; `diagnosing-bugs` for the fixer and bug hunter;
`code-review` for the reviewer; `to-tickets` (tickets with blocking edges)
for planning. Nothing estimates token budgets. The user-invoked orchestration
skills (`implement-spec`, `triage`) overlap DSH Superagent. Untested: whether
headless Claude Code with `dontAsk` loads skills from `.claude/skills/`.

**codebase-memory-mcp** (v0.12.1, pre-1.0) is a static C binary that builds a
SQLite graph of code, docs and infrastructure for 164 languages, including
Dockerfiles, Kubernetes, Terraform and CI, with `get_architecture`,
`trace_path`, `detect_changes`, `query_graph` and 13 more tools
([README](https://github.com/DeusData/codebase-memory-mcp)). The index lives in
`~/.cache/codebase-memory-mcp/` (`CBM_CACHE_DIR`); it auto-starts a shared
daemon and has no documented container recipe. Alternatives:
[codegraph](https://github.com/colbymchenry/codegraph) (MIT; its README lists
DeepSeek Harness among the clients it configures) and
[Serena](https://github.com/oraios/serena) (LSP-based, also edits; GPL for the
app).

**Mastra** (`@mastra/core@1.84.0`) has typed workflows (`.then`, `.parallel`,
`.branch`, `.dountil`, suspend and resume with persisted snapshots)
([control flow](https://mastra.ai/docs/workflows/control-flow.md)), wraps
external coding agents as tools or subagents through ACP or the Claude Agent
SDK ([ACP](https://mastra.ai/docs/connections/acp.md),
[SDK agents](https://mastra.ai/docs/connections/sdk-agents.md)), routes
`"provider/model"` strings with fallbacks and per-request model swaps
([agents](https://mastra.ai/docs/agents/overview.md)), and has a local Studio
UI and tracing ([Studio](https://mastra.ai/docs/studio/overview.md)).
*Inference*: it could run implement → UAT → review → merge as explicit steps
around `dsh` and `claude`, but that makes a second orchestrator with its own
state next to DSH Superagent. Borrow the workflow shape and the trace view.

**Pi** moved from `badlogic/pi-mono` to
[earendil-works/pi](https://github.com/earendil-works/pi)
(`@earendil-works/pi-coding-agent`, v1.5.2). It has JSON, RPC and SDK modes
for being one step of an orchestrator
([json](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/json.md)),
DeepSeek built in, MCP support, and "virtual models": an extension routes each
request to a physical model and thinking level
([virtual models](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/virtual-models.md)).
It has no native subagents and recommends running in a container; a failed
response does not set a nonzero exit code, so a wrapper must read the events.
*Inference*: a cheap, scriptable runtime for the bug hunter, effort evaluator
or architect, called from a wrapper like `claude-review`.

**Wayfinder Atlas** ([source](https://github.com/bbtgnn/wayfinder-atlas)) is
our own SvelteKit prototype rendering wayfinder maps (the format of
mattpocock/skills' `wayfinder` skill): tickets and blocker edges with Svelte
Flow and ELK/dagre, with missing-blocker and cycle warnings. Today it reads
Markdown at build time through Velite, has statuses `open | claimed |
resolved` only and no library export; `WayfinderView.svelte` takes
`projections` as props, so the view lifts out cleanly. To render superagent
work it needs a projection from GitHub issues (`Depends on #…` maps directly
to `blockedBy`), ticket states (waiting, running, blocked, in review,
merged) and live data instead of a build-time import.

## 5. Proposed direction

### Two sides joined by the issue

Keep DSH Superagent as the only orchestrator of delivery, and put new roles
around it rather than inside it:

- **Planning side** (local, a person in the loop, high tier): architect,
  strategic planner, task-effort evaluator. They read the specification and a
  code graph (codebase-memory-mcp) and produce issues in the existing format
  (`@mention`, acceptance criteria, `## User Acceptance Tests`,
  `Depends on`). This already happens informally ("ask any coding agent to
  read that `AGENTS.md`… and open the issues"); packaging it as skills makes
  it a defined role.
- **Delivery side** (the deployment, unattended, mid tier): implementer and
  its variants, fixer, UAT tester, reviewer. Variants and the bug hunter
  become DSH presets that the worker spawns as subagents, which needs no
  fork.
- **The issue is the interface** between the two, as it is today. A bug
  hunter that finds problems opens issues too, and they go through the same
  pipeline.

The reviewer and tester stay commands with exit-code contracts. Mastra is not
needed: the improvement is in how those commands are called and what they
return.

### Better calls and outputs for the checks

Today the worker assembles each call from the Delivery requirements:
`prompts/delivery.md` (49 lines) and `prompts/delivery-uat.md` spell out how
to find the PR number and head SHA, the order (UAT, then review), what each
exit code means, when to retry, how many failed reviews are allowed, and the
merge conditions. `claude-review` already enforces part of the order (exit 4
without a UAT pass) and the gate counts UAT rounds, but the rest is the
model's to follow. Proposals, all inside this repository:

- **One entry point that derives its arguments.** `superagent check` in the
  ticket directory finds the repository (`DSH_SUPERAGENT_GITHUB_REPOSITORY`),
  the PR for the current branch and its head SHA itself, runs UAT and then the
  review, and returns one verdict. The model no longer copies 40-character
  SHAs or chooses the order.
- **`superagent merge`** checks every merge condition in code (head and title
  equal to the last passing review, `uat-test --verify`, required checks, no
  changes requested) and runs `gh pr merge --match-head-commit`, or says which
  condition failed.
- **Budgets and retries in code.** Count failed reviews from
  `~/.local/state/claude-review/<repo>/pr-<n>/` as the gate counts UAT rounds,
  instead of from `orchestrator_report` records across workers; retry a
  transient failure once inside the command instead of asking the model to.
- **One exit-code scheme for every check**: today `2` means "no usable review"
  for `claude-review` but `BLOCKED` for `uat-test`, and `3` differs too.
  Proposed: 0 pass, 1 fail (fix and push), 2 blocked (stop), 3 transient (run
  again), 4 precondition not met (run the named step first).
- **Compact, structured output.** Both commands print the whole report and
  then tell the worker to read the file because the output "may be cut
  short", so a long review enters the context twice. Print one short result
  instead (verdict, SHA, rounds used and left, the next step, the file path)
  and keep the details in the file, read only on a fail. The fix plan can be
  emitted as numbered steps the worker loads into its todo list.
- **Shared code.** The head check, PR comment posting and truncation, history
  files and the disclosure line are duplicated in both scripts; one small
  module holds them, and a future check (bug hunter, effort evaluator) uses
  the same contract.

The Delivery requirements then shrink to "run `superagent check`, follow its
next step", which also saves context in every worker.

### One roles file, compiled into today's surfaces

A deployment-owned `superagent.yaml` plus one instructions file per role.
`superagent-configure` (or a build step) turns it into the profile patch
(presets, `allowedAgentPresets`, MCP rows), settings and prompts through the
management API, `claude-review.json`, and the tester profile. Sketch:

```yaml
version: 1
tiers: # what each tier means in this deployment, per runtime
  high: { claude: opus, dsh: { provider: deepseek-official, model: <high model> } }
  mid: { claude: sonnet, dsh: { provider: deepseek-official, model: deepseek-flash } }
  low: { dsh: { provider: deepseek-official, model: deepseek-flash, reasoningEffort: low } }

roles:
  implementer: # DSH Superagent worker: settings/executor + Delivery requirements
    runtime: dsh-worker
    tier: mid
    instructions: roles/implementer.md
    subagents: # DSH presets the worker may spawn (allowedAgentPresets)
      frontend: { tier: mid, instructions: roles/frontend.md }
      devops: { tier: mid, instructions: roles/devops.md }
      explorer: { tier: low, instructions: roles/explorer.md }
  reviewer: # scripts/claude-review
    runtime: claude-code
    tier: high
    route: { tier: mid, when: { diff_lines_below: 200 } }
    instructions: claude-code/review-prompt.md
  uat-tester: # uat-gate, dsh headless
    runtime: dsh-headless
    tier: mid
    instructions: prompts/uat-tester.md
  architect: # planning side, run locally
    runtime: local
    tier: high
    sources: [codebase-memory-mcp, docs/specification.md]
```

Roles name tiers, not models, so swapping a provider is one edit.

### Model routing by task complexity, in order of effort

1. **Reviewer by diff size** in `claude-review`: our code, no upstream change.
2. **Worker subagents by task**: allowed routes per tier, and the worker
   prompt says which tier a kind of subtask gets (exploration and search low,
   implementation mid). No fork; prompt-enforced.
3. **Effort at planning time**: the effort evaluator writes the estimate on
   the issue (a line or label), borrowing Gestalt's per-task model fields;
   the worker reads it to choose subagent tiers.
4. **Executor model per ticket**: needs upstream support (today one
   `executor.model` for every worker).

### Installable in your repository, or in your web app

**The CMS model.** [Payload](https://payloadcms.com/docs/getting-started/installation)
and [EmDash](https://docs.emdashcms.com/existing-project/) (Cloudflare's
Astro-native CMS) install the same way:

- a package plus one integration hook in the host app: `withPayload` in
  `next.config`, `emdash()` in `astro.config`; the code stays in
  `node_modules`;
- config as code in the host repository: Payload's typed
  `payload.config.ts`, whose plugins are functions from config to config;
- an admin UI under a reserved path of the host app (`/admin`,
  `/_emdash/admin`), served by the same process;
- generated types committed in the repository (`payload-types.ts`,
  `emdash-env.d.ts`);
- upgrades as one package bump plus migrations. `npx upgrade-emdash` also
  syncs the project's agent skills and writes `.emdash/UPGRADE.md` as a work
  order for an agent
  ([docs](https://docs.emdashcms.com/deployment/updating/)). EmDash also
  ships an MCP server.

**Applied to superagent** (*Inference*):

- `npm i superagent` plus `superagent.config.ts` in the target repository:
  typed roles, tiers and skills, with plugins such as a bug hunter or a
  preview environment written as config functions. `superagent init`
  scaffolds `AGENTS.md`, `.superagent/uat.yaml` and `.agents/skills/` (DSH
  workers load those natively).
- The dashboard mounts as a route of the host app (`/_superagent`) in a
  Next.js, Astro or SvelteKit integration, or stays the standalone dashboard
  for other stacks.
- `npx upgrade-superagent` bumps the package, syncs skills and writes a work
  order the agents then implement as an ordinary issue.
- What does not move into the app: the agents themselves. Workers, the UAT
  runner and the reviewer are long-running and privileged, so they remain a
  deployment (Coolify today); the app's integration talks to it.
- The trust rule still holds: the deployment reads `superagent.config.ts`
  from the protected default branch, never from the pull request under
  review, and the reviewer and tester settings (model, prompt, permissions)
  can be set only there or in the deployment. A PR that changes the config
  takes effect after it merges.

### UI

The dashboard's Caddy already rewrites the Host header and checks the DSH
session cookie for `/orchestrator/api/*` (`dashboard/Caddyfile`), so a static
page added to that container can read `tickets`, `tickets/:id`, `threads` and `decisions`
with the browser's cookie, with no new proxy. Sources for a "what is
happening" view:

- Superagent tickets and stages (undocumented routes: pin the prototype ref).
- The `Depends on` graph from GitHub issues, drawn with Svelte Flow and ELK
  (the Wayfinder Atlas layout approach).
- UAT runs and verdicts from the gate's database, and review rounds from
  `~/.local/state/claude-review/`.

Worker transcripts have no HTTP route; the panel's session button stays the
way to open them.

**Multi-user, then multiplayer.** Work is already multi-user at the GitHub
layer (allowlisted people open issues and steer with comments), but the UI is
single-user: one DSH launch token mints one shared session cookie, so the UI
cannot tell who is looking or acting. Making it explicit:

- show on each ticket who requested it, who commented or redirected it, and
  which agent account acts;
- per-person sign-in (GitHub OAuth against the same allowlist) in the
  dashboard instead of sharing the launch URL;
- multiplayer: live updates (server-sent events or a WebSocket from the
  dashboard), presence on a ticket or on the dependency map, and actions
  (pause, retry, comment) attributed to the person who took them.

### Context: every agent in the smart zone

Model quality drops as a context window fills, so each agent should do its
work well before that point. Mechanisms, from cheapest:

- **Compact check output** (above): no review or UAT report enters a
  worker's context twice, and details stay in files.
- **Subagents as context isolation**: exploration and search run in a
  low-tier subagent that returns a summary, which workers can already do with
  `subagent`.
- **Graph queries instead of text floods**: codebase-memory-mcp answers
  "who calls this" without reading whole files.
- **Large outputs out of the context**: Gestalt's `context-mode` routes
  command, log and browser output into an indexed store (its license, ELv2,
  allows self-hosting but not offering it as a service).
- **Budgets at planning time**: the effort evaluator sizes each issue to fit
  the ~150k-token budget before it becomes a ticket.
- **Measure it**: show each worker's context use in the UI next to its ticket,
  and tune DSH's compaction plugin from real numbers.

## 6. Open questions

- Does headless Claude Code with `dontAsk` load `.claude/skills/`, and do the
  managed settings need `Skill(...)` rules?
- Does codebase-memory-mcp run in the image (daemon, cache directory,
  indexing at checkout), or is codegraph easier with DSH?
- Is a code-enforced delivery order worth a fork of dsh-superagent-prototype,
  or is an upstream proposal (a stage list per ticket) realistic?
- Which role goes first as a pilot: the reviewer routed by diff size, a
  bug-hunter preset, or the planning-side architect skill?

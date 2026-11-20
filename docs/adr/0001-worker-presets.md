# Worker role presets via profile override

**Status:** accepted (F5.1 spike)

Workers already nest `@deepseek-ai/dsh-tool-subagent` under the
`dsh-superagent-worker` preset (`modelSelection: true`, `agentPreset: standard`).
F5 needs extra presets (`explorer`, `frontend`, `devops`) allowlisted on that
nested row, plus Host-authorized model routes for cheaper child calls. This
ADR records which deployment mechanism actually changes that nested row.

## Decision

**Use the profile patch** (`$DSH_HOME/profiles/superagent/cordis.patch.yml`):

1. `insert` each role as an `@deepseek-ai/dsh-agent-preset` row (new `id` /
   preset `config.id`).
2. Override the existing row `- id: dsh-superagent-worker-preset` and restate
   its full `config` (including `plugins`), adding `allowedAgentPresets` on the
   nested `tool-subagent` so the allowlist includes those role ids (and
   `standard` when it remains the default `agentPreset`).

Do **not** target `- id: tool-subagent` alone: that id is the Host/base row
(disabled under the web composition). It is not the worker’s nested tool.

Loader rule that drives this shape: a patch keyed by `id` replaces that row’s
`config` wholesale (no deep merge). Restate every field the worker preset
needs. See DSH `composition-reference` (Loader patch dialect).

## Evidence (`dsh --profile superagent --dump-config`)

Ran against image `console-superagent:latest` (dsh `0.2.2-rc.5`, plugin linked
from `/opt/dsh-superagent`) — same seeding path as this repo’s `Dockerfile`.

### A — top-level `tool-subagent` does not reach the worker

Profile appended:

```yaml
- id: tool-subagent
  config:
    provider: spawn
    toolName: subagent
    modelSelection: true
    modelDiscovery: true
    agentPreset: standard
    allowedAgentPresets:
      - standard
      - explorer-spike
```

Dump: Host `tool-subagent` gained `allowedAgentPresets` / `explorer-spike`
(one hit in the whole dump). The
`- id: dsh-superagent-worker-preset` nested `tool-subagent` still had only
`agentPreset: standard` — **zero** `allowedAgentPresets` under that preset.

### B — overriding `dsh-superagent-worker-preset` works (chosen)

Same profile kept `webserver` / `dsh-superagent`, then overrode
`dsh-superagent-worker-preset` (full plugins list from the plugin’s
`cordis.patch.yml`, with `allowedAgentPresets` added) and `insert`ed
`explorer-spike-preset`. Dump markers and excerpts:

```text
# == dsh-superagent, patched by /home/agent/.dsh/profiles/superagent/cordis.patch.yml
- id: dsh-superagent-worker-preset
```

```yaml
          - id: tool-subagent
            name: '@deepseek-ai/dsh-tool-subagent'
            config:
              provider: spawn
              toolName: subagent
              modelSelection: true
              modelDiscovery: true
              agentPreset: standard
              allowedAgentPresets:
                - standard
                - explorer-spike
```

```yaml
- id: explorer-spike-preset
  name: '@deepseek-ai/dsh-agent-preset'
  config:
    id: explorer-spike
    name: explorer-spike
```

### C — build-time plugin patch also works (fallback only)

With a clean profile (only `webserver` / `dsh-superagent`) and
`allowedAgentPresets` edited into `/opt/dsh-superagent/cordis.patch.yml`, dump
showed the same nested allowlist under `dsh-superagent-worker-preset`. Prefer
the profile path so F5.2 keeps a reviewable `dsh/profile.patch.yml` instead of
mutating the cloned plugin.

## Subagent model routes (Host model-selection settings)

Worker `tool-subagent` already sets `modelSelection: true` / `modelDiscovery:
true`. Authorization is **not** that tool config: each explicit child route
and `list_subagent_models` read the Host service from
`@deepseek-ai/dsh-tool-subagent/model-selection-settings` (dump id
`subagent-model-selection-settings`, shipped by `@deepseek-ai/dsh-web-app` with
no config override).

Defaults: `enabled: false`, `allowedModels: []`. When disabled or unmounted,
selection allows only the parent Agent’s current provider/model (and its
efforts). When enabled, the setting’s exact `allowedModels` route list is the
allowlist; empty/malformed/repeated routes reject with
`INVALID_MODEL_SELECTION_SETTINGS`; a route outside the list is
`MODEL_ROUTE_NOT_ALLOWED`.

F5.3 enables this Host setting via the profile patch on
`subagent-model-selection-settings`, with `enabled` and `allowedModels` read
from `SUPERAGENT_TIER_LOW_*` / `SUPERAGENT_TIER_MID_*` through `!!js` at DSH
load (same env pattern as the UAT tester profile). `superagent-configure`
validates those variables on start but does not write the Host setting — the
orchestrator management API has no block for it. Preset allowlists and Host
routes are separate gates.

## Fallback

If a future dsh/plugin change makes the
`dsh-superagent-worker-preset` profile override fail (id rename, dump no
longer shows `patched by` the profile, or nested `allowedAgentPresets`
missing), patch the cloned plugin at image build time: after
`git clone` / `pnpm build` of `/opt/dsh-superagent`, edit its
`cordis.patch.yml` (same nested `tool-subagent` fields) before
`dsh plugin --profile superagent add 'link:/opt/dsh-superagent'`. Experiment C
confirmed that path. Treat it as a last resort: it forks deployability from
upstream plugin files and is harder to review than `dsh/profile.patch.yml`.

## Consequences for F5.2

- Move today’s `printf` profile seed into `dsh/profile.patch.yml`.
- Generate or maintain a full restatement of `dsh-superagent-worker-preset`
  when changing nested delegation config.
- Add role preset inserts + `allowedAgentPresets`; leave Host model routes to
  F5.3.

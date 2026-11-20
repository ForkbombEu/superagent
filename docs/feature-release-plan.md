# Feature release plan: check tools, agent roles, Opus console

Sequenced from [extending-superagent.md](extending-superagent.md). Features
are Gestalt L1 milestones, issues are L2 steps
([plan format](https://github.com/dyne/gestalt-agents/blob/HEAD/plugins/gestalt/skills/org-plan/references/plan-format.md)),
adapted to GitHub issues and squash merges. A local `.gestalt/*.org` plan may
mirror this one for supervised runs; never commit `.gestalt/`.

## 1. Review of the first draft

The first draft had the right order (check contract first, roles next,
console and packaging after) but several features rested on assumptions the
code does not support. Each finding below changed the detailed plan.

1. **F0 is already done** on `feat/agent-roles` (DSH `0.2.2-rc.5`, tester
   profile, 7 tester tests green). The repository has **no CI**, so "testable
   at every layer" had nothing enforcing it. F0 now adds a CI workflow.
2. **A synchronous `superagent check` would break UAT recovery.** Workers run
   `uat-test` as a background job, report `Waiting for UAT: PR #<n> at <sha>`
   and end their turn; `superagent-uat-recovery` parses that marker and
   retries the ticket when the gate finishes. A retried worker re-running a
   combined command could also start a second Claude review of the same
   commit. F2 is now an **idempotent "next step for this head"** command with
   stored per-commit results, a per-PR lock, and the recovery marker updated
   in the same feature.
3. **Exit-code unification needs review reuse.** Mapping `claude-review`'s
   "no usable review" and "not posted" to a common *retry* code is only safe
   if a retry reuses a saved review for that commit instead of paying for a
   new one. F1 adds that, and keeps each command's last output line so the
   Delivery prompts stay valid between issues.
4. **There is no profile-patch file to extend.** The deployment's DSH profile
   patch is a `printf` in the `Dockerfile` (webserver and state dir only).
   Worker presets live in the plugin's own `cordis.patch.yml`, and whether our
   patch can override its nested `tool-subagent` row is unverified. F5 starts
   with a spike, with a build-time patch of the plugin as the fallback.
5. **A roles file compiles at two moments.** Presets and model routes are
   build-time (profile patch); executor, reviewer and tester models are
   start-time (`superagent-configure`, `claude-review.json`, the `uat-gate`
   environment). F6 splits along that line.
6. **The console is a new service.** The dashboard is only Caddy. Creator chat
   needs a Node service behind the same cookie check, its own Anthropic API
   key, and GET-only access to the management API (which trusts any request
   that looks like loopback).
7. **A bug hunter cannot file its own work.** Issues the agent account opens
   are not requests ("the agent does not take requests from itself"). Findings
   become unmentioned, labelled issues that a person promotes with a comment.
8. **The ticket map needs issue bodies** for `Depends on`, which needs a
   server-side GitHub read token. It moves into the console service.
9. **The installable package** depends on the roles-file shape; it is kept as
   an outline to re-plan after F6.
10. **Budgets were guesses.** Every issue now lists the files it loads, so the
    100k–200k token budget can be checked before work starts.

## 2. Decisions

| # | Decision | Why |
| - | -------- | --- |
| D1 | `attesta` is the backbone: the deployable epic branch every feature lands on. Each feature gets an integration branch `feat/f<N>-<slug>` from `attesta`; each issue a branch `f<N>/<slug>` from it. Issue PRs squash-merge into the integration branch; the feature PR squash-merges to `attesta` with the feature's title. A person promotes `attesta` to `main`. | Coolify deploys `attesta`, so every feature is tried on a live deployment before `main`; `attesta` only ever receives consistent features; each squash commit names one tangible win. |
| D2 | People and local agents implement this backlog, not the deployment's own agent account (accepted). | The Delivery procedure merges into the PR's base without a feature-branch rule, and several tests need Docker. Revisit once F3 exists. |
| D3 | Checks are idempotent per commit: a result stored for a SHA is reused, never re-bought. | Makes retries, lost wake-ups and `superagent check` safe. |
| D4 | One `console` service (accepted): assistant-ui + AI SDK (Next.js) hosts creator chat and later the ticket map (React Flow + elkjs, porting Wayfinder's projection logic). | One build, one auth path, one dependency tree: less code than a React chat plus a separate Svelte map. |
| D5 | One Anthropic key for the deployment: the console reads the same `ANTHROPIC_API_KEY` the reviewer uses. A deployment configured only with `CLAUDE_CODE_OAUTH_TOKEN` runs without the console chat. | One secret to manage. AI SDK calls the Anthropic API, which takes an API key; the OAuth token only works through Claude Code. |
| D6 | Delivery gates stay CLI check tools. No `subagent_claude_code`, no fork of `WORK_TOOLS`. | Keeps the exit-code contract and policy in our code. |

## 3. How work is split

| Gestalt | Here | State | Size |
| ------- | ---- | ----- | ---- |
| L1 milestone | **Feature**: an integration branch, merged to `attesta` as one squash commit | Consistent on `attesta`: builds, tests pass, the feature's demo works on the deployment | One acceptance outcome; 2–5 issues |
| L2 step | **Issue**: one GitHub issue, one PR into the feature branch | May leave the feature branch inconsistent (unused module, prompt behind the code) when the next issue in the same feature fixes it | **100k–200k tokens** for its implementer, including tests and fixes |

Splitting rules:

- Split when two outcomes can be accepted separately; keep a producer and its
  consumer in one issue when either alone would be an invalid contract.
- An issue must fit the smart zone: the files listed under **Context** plus
  the change and its tests. Estimate about 12 tokens per line read. An issue
  that needs more than ~40k tokens of context, or more than one subsystem, is
  split.
- Mid-tier models get issues up to ~150k; high-tier (Opus) design issues up to
  ~200k. A design issue produces a short ADR in `docs/adr/` before the
  implementation issues start.
- The last issue of every feature restores consistency: prompts, README,
  `.env.example` and tests agree with the code.
- Escalate a model only on an observable trigger (the tests cannot express the
  contract, two failed attempts, a trust boundary); never silently.

Issue body (Gestalt L2 fields, in the existing `templates/AGENTS.md` format):

```markdown
<one paragraph: what and why, naming this plan's issue ID>

- Effort :: ~100k
- Model :: mid
- Context :: scripts/claude-review, uat/tests/test_gate.py
- Branch after :: consistent | inconsistent until F1.4

## Acceptance criteria
- [ ] <Change, as verifiable behavior>
- [ ] <Tests>

Depends on #<a>
```

## 4. Feature map

Squash history on `attesta`, in order:

```
F0  build: run on dsh 0.2.2-rc.5 with tests in CI
F1  feat(checks): share one exit-code contract and compact output
F2  feat(checks): add superagent check, the next step for a pull request head
F3  feat(checks): add superagent merge with every merge condition in code
F4  feat(review): pick the Claude reviewer model by diff size
F5  feat(roles): let workers delegate to role presets on tiered models
F6  feat(roles): configure the deployment from superagent.yaml
F7  feat(console): chat with Opus about the deployment's design
F8  feat(checks): hunt bugs and file them for a person to promote
F9  feat(templates): ship planning skills for target repositories
F10 feat(console): show the ticket dependency map
F11 feat(pkg): scaffold a target repository with superagent init
```

```
F0 ─┬─ F1 ─┬─ F2 ─┬─ F3
    │      │      └─ F8
    │      └─ F4
    ├─ F5 ── F6 ── F11
    ├─ F7 ── F10
    └─ F9
```

Core is F0–F3: after them, every check and the merge are code with one
contract, and the Delivery prompts are short. F4–F6 add model routing and
roles. F7 and F10 are the console. F8, F9 and F11 extend outward.

---

## F0 — Platform baseline

- **Squash:** `build: run on dsh 0.2.2-rc.5 with tests in CI`
- **Goal:** the deployment runs on current DSH, and every PR runs the test
  suite automatically.
- **Skills:** none beyond the repository's conventions.
- **Consistent when:** images build; CI is green on the feature PR.
- **Verify on `attesta`:** CI check on the merge commit; a local
  `docker compose build`; launch URL opens; `/orchestrator/api/state` answers.
- **Branch:** `feat/f0-baseline`.

### F0.0 Add the research notes and this plan

- **Depends:** — · **Model:** — · **Effort:** done · **Branch after:** consistent (docs only)
- **Change:** `docs/extending-superagent.md` and `docs/feature-release-plan.md`.
- **Done when:** the plan is reviewable next to the code it plans.

### F0.1 Bump DSH to 0.2.2-rc.5

- **Depends:** — · **Model:** low · **Effort:** ~60k · **Branch after:** consistent
- **Context:** `Dockerfile`, `uat/tester/Dockerfile`, `uat/tester/profile.patch.yml`, README tester line (already changed, uncommitted).
- **Why:** current DSH; `tool-agent-team` handled; prerequisite for presets.
- **Change:** commit the existing diff; no further edits.
- **Tests:** `python3 -m unittest discover -s uat/tests -t .`, at least the 7 `test_dsh_tester` tests (macOS: `TMPDIR` set to its realpath).
- **Done when:** both images build; tester profile patch applies; plugin starts.

### F0.2 Run the tests on every pull request

- **Depends:** F0.1 · **Model:** low · **Effort:** ~80k · **Branch after:** consistent
- **Context:** `uat/tests/docker_support.py` (skip logic), the test command in README section 5.
- **Why:** each layer must be verifiable without trusting a local run.
- **Change:** `.github/workflows/test.yml`: Python unit tests on every PR and every push to `attesta` and `main`; one job with `SUPERAGENT_UAT_SKIP_DOCKER_TESTS=1`, one job with Docker running the container tests; `docker compose build` of the superagent image.
- **Tests:** the workflow itself; a deliberately failing test fails it (checked once, not committed).
- **Done when:** the check is green on this PR and required on `attesta` and `main` (branch protection set by a person).

---

## F1 — One check contract *(core)*

- **Squash:** `feat(checks): share one exit-code contract and compact output`
- **Goal:** `uat-test` and `claude-review` exit with the same codes, print one
  short result, keep details in a file, and never pay twice for one commit.
- **Skills:** `codebase-design`, `tdd`.
- **Contract:** `0` pass · `1` fail (fix and push) · `2` blocked (stop) ·
  `3` retry (run the same command again) · `4` precondition (run the named
  step first). Last line unchanged: `<command>: <VERDICT> for <sha>`.
- **Consistent when:** both commands on the shared module; Delivery prompts
  describe the same codes; tests green.
- **Verify on `attesta`:** run each command against the test fakes (`fake_gh`,
  the in-process gate); stdout is a few lines; `echo $?` matches the table.

### F1.1 Add the shared check module

- **Depends:** F0 · **Model:** mid · **Effort:** ~90k · **Branch after:** inconsistent (module unused)
- **Context:** `scripts/claude-review` (168 lines), `scripts/uat-test` (203), `Dockerfile` COPY block.
- **Why:** history paths, disclosure line, comment truncation, head checks and result printing are duplicated in both scripts.
- **Change:** `scripts/superagent_checks.py` with `Exit` codes, a `Result` (check, verdict, sha, rounds used and allowed, next step, detail file), `print_result`, `pr_comment` (disclosure, truncation, posted marker per result), `history_dir(check, repo, pr)` and `require_head(repo, pr, sha)`; installed next to the scripts in the image.
- **Tests:** `uat/tests/test_checks.py`: result formatting, truncation, posted-marker idempotency, head mismatch → `4`.
- **Done when:** unit tests pass; image builds with the module importable from `/usr/local/bin`.

### F1.2 Move uat-test onto the module

- **Depends:** F1.1 · **Model:** mid · **Effort:** ~100k · **Branch after:** consistent (exit codes and last line unchanged for UAT)
- **Context:** `scripts/uat-test`, `superagent_checks.py`, `uat/tests/test_gate.py` (the uat-test client tests, ~150 lines).
- **Why:** UAT already uses 0/1/2/3; it gains compact output and shared helpers.
- **Change:** use the module for history, comment and printing; print the compact result instead of the whole report (the report stays in the file and on the PR); keep `uat-test: <VERDICT> for <sha>` as the last line.
- **Tests:** existing uat-test client tests pass with updated stdout assertions; a test that a full report is not on stdout.
- **Done when:** the worker-visible behavior differs only in output length.

### F1.3 Move claude-review onto the module and reuse stored reviews

- **Depends:** F1.1 · **Model:** mid · **Effort:** ~150k · **Branch after:** inconsistent (prompt still lists old codes 2 and 3)
- **Context:** `scripts/claude-review`, `superagent_checks.py`, `uat/tests/test_gate.py` (the claude-review guard test), `uat/tests/fake_gh.py`.
- **Why:** "no usable review" and "not posted" become *retry*, which is safe only if a retry reuses the saved review.
- **Change:** store each review with its sha, title and verdict; when a review for this sha and title exists, reuse it (post it if its comment was not posted) instead of running Claude; map old `2`→`3`, old `3`→`3`, keep `4`; compact result names the fix-plan file on a fail and the failed reviews so far.
- **Tests:** a fake `claude` on `PATH` returning canned JSON: pass, fail, malformed answer (→3), post failure then rerun (→ reposts, Claude not called again), title change (→ new review).
- **Done when:** two runs on one commit call Claude once.

### F1.4 Align the Delivery prompts and README

- **Depends:** F1.2, F1.3 · **Model:** low · **Effort:** ~60k · **Branch after:** consistent
- **Context:** `prompts/delivery.md`, `prompts/delivery-uat.md`, README sections 4–5, `uat/tests/test_gate.py` configure test.
- **Why:** the model must follow the code, not the old table.
- **Change:** one exit-code table referenced by both procedures; "read the detail file only on a fail"; README documents the contract.
- **Tests:** configure test still assembles both prompts; a test that both prompt files name every exit code.
- **Done when:** no prompt mentions an exit code the commands no longer return.

---

## F2 — `superagent check` *(core)*

- **Squash:** `feat(checks): add superagent check, the next step for a pull request head`
- **Goal:** in a ticket directory, one command finds the repository, PR and
  head, runs the next missing check for that head (UAT, then review), and
  says what to do next. Workers stop copying SHAs and choosing the order.
- **Skills:** `codebase-design`, `tdd`.
- **Protocol kept:** the worker still runs it as a background job and reports
  a waiting marker; a lost wake-up is still recovered.
- **Consistent when:** prompts describe only `superagent check`; recovery
  understands the new marker; tests green.
- **Verify on `attesta`:** in a checkout against the fakes, call `superagent
  check` three times: UAT runs, review runs, then the stored pass is
  reported without new work.

### F2.1 Derive repository, PR and head

- **Depends:** F1 · **Model:** mid · **Effort:** ~80k · **Branch after:** inconsistent (command not in prompts)
- **Context:** `superagent_checks.py`, `uat/tests/fake_gh.py`, the env vars Superagent sets (`DSH_SUPERAGENT_GITHUB_REPOSITORY`, `DSH_SUPERAGENT_GITHUB_NUMBER`).
- **Why:** argument copying is where workers make mistakes.
- **Change:** `scripts/superagent` with `check --dry-run`: repository from the environment, PR from the current branch, head = `git rev-parse HEAD`, which must equal the PR head; prints the plan (which checks have results for this head).
- **Tests:** no PR → `4`; local HEAD behind the PR → `4` naming `git pull`; dry run lists stored results.
- **Done when:** `superagent check --dry-run` is correct for each case.

### F2.2 Run the next check, once per head

- **Depends:** F2.1 · **Model:** mid · **Effort:** ~150k · **Branch after:** inconsistent (prompts unchanged)
- **Context:** `scripts/superagent`, `superagent_checks.py`, `uat-test` and `claude-review` entry points, `test_gate.py` gate harness.
- **Why:** one verdict per head, and a lost wake-up must not start a second review.
- **Change:** a lock per PR; run `uat-test` when UAT is on and has no result for the head, else `claude-review` when it has none; aggregate into one compact result with `next:`; count failed reviews per PR from stored reviews (three → `2`).
- **Tests:** UAT fail stops before review; UAT pass → review runs; third run reuses both; concurrent second call waits on the lock; fourth failed review → blocked.
- **Done when:** the sequence above holds with the fakes.

### F2.3 Switch workers and recovery to superagent check

- **Depends:** F2.2 · **Model:** mid · **Effort:** ~100k · **Branch after:** consistent
- **Context:** `prompts/delivery.md`, `prompts/delivery-uat.md`, `scripts/superagent-uat-recovery`, `uat/tests/test_uat_recovery.py`, `Dockerfile`.
- **Why:** the procedure shrinks to "run, report the marker, follow `next:`".
- **Change:** prompts: run `superagent check` in the background, report `Waiting for checks: PR #<n> at <sha>`, follow `next:`; recovery accepts the new marker and keeps the old ones; image installs `superagent`; README.
- **Tests:** recovery tests for the new marker; configure test; prompt test that `claude-review` and `uat-test` are no longer called directly.
- **Done when:** the review counting through `orchestrator_report` is gone from the prompt.

---

## F3 — `superagent merge` *(core)*

- **Squash:** `feat(checks): add superagent merge with every merge condition in code`
- **Goal:** a PR merges only when code has checked every condition; the
  worker no longer decides merge eligibility.
- **Skills:** `tdd`.
- **Consistent when:** the Delivery merge section is "run `superagent merge`,
  follow `next:`"; tests green.
- **Verify on `attesta`:** against the fakes, each failing condition is named;
  the passing case calls `gh pr merge` with `--match-head-commit`.

### F3.1 Check the merge conditions

- **Depends:** F2 · **Model:** mid · **Effort:** ~120k · **Branch after:** inconsistent (not in prompts)
- **Context:** `scripts/superagent`, `superagent_checks.py`, `prompts/delivery.md` merge section, `uat/tests/fake_gh.py`.
- **Why:** these conditions are prose today.
- **Change:** `superagent merge --dry-run`: re-read head and title; stored passing review for that head and title; `uat-test --verify` exit 0 when UAT is on; required checks passed (all checks when none required); no review requesting changes; draft → `next: mark ready`. Pending checks → `3`; any unmet condition → `4` with its name.
- **Tests:** extend `fake_gh` with `pr checks`, `pr ready`, reviews; one test per condition.
- **Done when:** every condition in the prompt has a test.

### F3.2 Merge and shrink the prompt

- **Depends:** F3.1 · **Model:** mid · **Effort:** ~90k · **Branch after:** consistent
- **Context:** `scripts/superagent`, `prompts/delivery.md`, `prompts/delivery-uat.md`, README section 4.
- **Why:** finish the move from prose to code.
- **Change:** run `gh pr merge --squash --match-head-commit <sha> --subject "<title> (#<n>)"`; a GitHub refusal → `2` with its message; prompt merge section replaced; README diagram updated.
- **Tests:** fake merge success and refusal.
- **Done when:** the prompt no longer lists merge conditions.

---

## F4 — Reviewer model by diff size

- **Squash:** `feat(review): pick the Claude reviewer model by diff size`
- **Goal:** small PRs are reviewed by a cheaper Claude model.
- **Skills:** `tdd`.
- **Consistent when:** configured from the environment and documented.
- **Verify on `attesta`:** two fixture PRs under and over the threshold show
  different models in the compact result and the PR comment.

### F4.1 Route the reviewer by diff size

- **Depends:** F1 · **Model:** low · **Effort:** ~80k · **Branch after:** consistent
- **Context:** `scripts/claude-review`, `scripts/superagent-entrypoint.sh` (claude-review.json), `compose.yaml`, `.env.example`.
- **Why:** cost; most PRs are small.
- **Change:** `SUPERAGENT_REVIEWER_SMALL_MODEL` and `SUPERAGENT_REVIEWER_SMALL_DIFF_LINES` (default 200) into `claude-review.json`; `claude-review` counts changed lines against the base and picks the model; the model appears in the result and comment; a stored review is reused only for the same model choice.
- **Tests:** selection unit tests; entrypoint JSON test; fake `claude` receives the expected `--model`.
- **Done when:** unset variables keep today's behavior.

---

## F5 — Worker role presets

- **Squash:** `feat(roles): let workers delegate to role presets on tiered models`
- **Goal:** a worker can delegate exploration, frontend or infrastructure
  subtasks to named presets on cheaper models, with no fork of Superagent.
- **Skills:** `research`, `prototype`, `writing-for-agents` (preset personas).
- **Consistent when:** presets exist, are allowlisted, have model routes, and
  the worker prompt says when to use each.
- **Verify on `attesta`:** in a worker session, delegating to `explorer` runs on
  the low-tier model; an unlisted preset is rejected.

### F5.1 Spike: can the deployment add presets to workers?

- **Depends:** F0 · **Model:** high · **Effort:** ~150k · **Branch after:** consistent (docs only)
- **Context:** `Dockerfile` profile seeding, the plugin's `cordis.patch.yml` (delegation group), DSH `tool-subagent` README (`allowedAgentPresets`, `modelSelection`), `/tmp/dsh-research` clones.
- **Why:** overriding the plugin's nested `tool-subagent` row from our patch is unverified.
- **Change:** in a built image, try the override with `dsh --profile superagent --dump-config`; check how subagent model routes are authorized (Host model-selection settings); write `docs/adr/0001-worker-presets.md` with the mechanism and, if the override fails, the fallback (patch the cloned plugin at build time).
- **Tests:** the dump-config output recorded in the ADR.
- **Done when:** the ADR names one mechanism that works.

### F5.2 Move the profile patch into a file and add presets

- **Depends:** F5.1 · **Model:** mid · **Effort:** ~120k · **Branch after:** inconsistent (no routes or prompt yet)
- **Context:** `Dockerfile`, the ADR, plugin `cordis.patch.yml`.
- **Why:** presets need a reviewable file, not a `printf`.
- **Change:** `dsh/profile.patch.yml` copied into the profile; presets `explorer`, `frontend`, `devops` with personas in `roles/*.md`; `allowedAgentPresets` on the worker's `tool-subagent`.
- **Tests:** a Docker test that dump-config contains the presets and allowlist; a unit test that every allowlisted preset is defined.
- **Done when:** the plugin starts with the new profile.

### F5.3 Model routes and worker guidance

- **Depends:** F5.2 · **Model:** mid · **Effort:** ~100k · **Branch after:** consistent
- **Context:** `scripts/superagent-configure`, `prompts/delivery.md`, the ADR, `.env.example`.
- **Why:** presets are only cheaper if their routes are.
- **Change:** model routes per tier from `SUPERAGENT_TIER_LOW_*` / `SUPERAGENT_TIER_MID_*`, applied where the ADR says; a short prompt section mapping subtask kinds to presets; README.
- **Tests:** configure unit test for routes; prompt test for the section.
- **Done when:** the verification above passes on a local deployment.

---

## F6 — `superagent.yaml`

- **Squash:** `feat(roles): configure the deployment from superagent.yaml`
- **Goal:** one deployment-owned file sets tiers, role models, reviewer
  routing and presets; environment variables still override it.
- **Skills:** `domain-modeling`, `codebase-design`, `tdd`.
- **Trust:** the file ships in the image or the deployment, never read from a
  pull request.
- **Consistent when:** build and start both read the file; docs list the
  mapping from environment variables.
- **Verify on `attesta`:** change a tier's model in the file, rebuild, and see it
  in settings, `claude-review.json` and the profile.

### F6.1 Schema and loader

- **Depends:** F5 · **Model:** mid (high if the schema is contested) · **Effort:** ~100k · **Branch after:** inconsistent (unused)
- **Context:** `docs/extending-superagent.md` roles sketch, `superagent-configure`, entrypoint, `dsh/profile.patch.yml`.
- **Change:** `superagent.yaml`, `superagent.schema.json`, a loader that validates and resolves tiers into concrete models, with env overrides.
- **Tests:** valid, invalid and override cases.
- **Done when:** the sample file resolves to today's effective settings.

### F6.2 Generate the profile patch at build time

- **Depends:** F6.1 · **Model:** mid · **Effort:** ~100k · **Branch after:** inconsistent (start-time still env-only)
- **Context:** loader, `Dockerfile`, `dsh/profile.patch.yml`.
- **Change:** the build renders the profile patch (presets, allowlist, routes) from the file.
- **Tests:** golden test: rendering the sample equals the committed patch.
- **Done when:** the image's profile is generated, not hand-written.

### F6.3 Apply the file at start

- **Depends:** F6.2 · **Model:** mid · **Effort:** ~120k · **Branch after:** consistent
- **Context:** loader, `superagent-configure`, `superagent-entrypoint.sh`, `compose.yaml` (uat-gate env), README, `.env.example`.
- **Change:** configure and the entrypoint read executor, reviewer and UAT settings from the file; uat-gate receives the UAT model from it; README table maps every variable to its key.
- **Tests:** configure and entrypoint tests with the file and with overrides.
- **Done when:** a deployment with only the file and secrets behaves like one with today's variables.

---

## F7 — Opus console

- **Squash:** `feat(console): chat with Opus about the deployment's design`
- **Goal:** an operator opens `/console`, chats with Opus about roles,
  presets and architecture with read access to tickets and settings, and
  copies out issue drafts in the repository's format.
- **Stack:** assistant-ui (`<Thread />`) + AI SDK (`@ai-sdk/anthropic`),
  scaffolded with the assistant-ui CLI (Next.js). We write the route, the
  system prompt and the tools.
- **Skills:** `writing-for-agents` (system prompt), `tdd`.
- **Trust:** deployment-owned key and prompt; tools are read-only or draft-only;
  no GitHub writes; secrets redacted from settings.
- **Consistent when:** service in compose, routed and authenticated, documented.
- **Verify on `attesta`:** signed-in browser chats with Opus about a ticket;
  signed-out request gets 401; a drafted issue matches the template.

### F7.1 Brief: what the console is for

- **Depends:** F0 · **Model:** high (Opus, with a person) · **Effort:** ~100k · **Branch after:** consistent (docs only)
- **Context:** this plan, `extending-superagent.md` sections 1 and 5, `templates/AGENTS.md` "Writing issues".
- **Change:** `docs/console.md`: questions it answers, data it may read, tools, what "draft" means, credential (D5), retention.
- **Done when:** a person accepts the brief.

### F7.2 Console service behind the dashboard

- **Depends:** F7.1 · **Model:** mid · **Effort:** ~130k · **Branch after:** inconsistent (stub runtime)
- **Context:** `compose.yaml`, `dashboard/Caddyfile`, assistant-ui CLI output.
- **Change:** `console/` from the assistant-ui scaffold with base path `/console`, a stub runtime; compose service (internal only); Caddy route with the same `forward_auth` as the management API.
- **Tests:** a compose smoke script: `/console` 401 without cookie, 200 with one.
- **Done when:** the Thread renders behind the dashboard.

### F7.3 Stream Opus

- **Depends:** F7.2 · **Model:** mid · **Effort:** ~100k · **Branch after:** inconsistent (no tools)
- **Context:** `console/` route and runtime, AI SDK Anthropic provider docs.
- **Change:** chat route with `streamText`; the deployment's `ANTHROPIC_API_KEY` passed to the console service only (the superagent service keeps unsetting it from worker shells) and `SUPERAGENT_CONSOLE_MODEL`; without the key, the page says the chat is not configured; system prompt file in the image.
- **Tests:** route test with the AI SDK mock model; a test that a missing key gives the "not configured" answer.
- **Done when:** replies stream in the Thread.

### F7.4 Read-only and draft tools

- **Depends:** F7.3 · **Model:** mid · **Effort:** ~130k · **Branch after:** inconsistent (docs pending)
- **Context:** `console/` route, orchestrator API routes (`state`, `tickets`, `tickets/:id`, `settings`, `prompts`), `templates/AGENTS.md` issue template.
- **Change:** tools `list_tickets`, `get_ticket`, `get_settings` (redacted), `get_prompts`, `draft_issue` (returns Markdown); the API client sends GET only.
- **Tests:** tool unit tests; a test that the client refuses non-GET; redaction test.
- **Done when:** Opus answers "what is ticket X waiting for" from live data.

### F7.5 Persistence and docs

- **Depends:** F7.4 · **Model:** low · **Effort:** ~70k · **Branch after:** consistent
- **Context:** `console/`, README, `.env.example`, `compose.yaml`.
- **Change:** threads kept in the browser (assistant-ui local storage); README section; env documented.
- **Done when:** reload keeps the thread; README explains setup and trust.

---

## F8 — Bug hunter

- **Squash:** `feat(checks): hunt bugs and file them for a person to promote`
- **Goal:** a scheduled or requested bug hunt on the default branch files
  deduplicated issues; a person turns one into work with an `@` comment.
- **Skills:** `diagnosing-bugs`, `tdd`.
- **Consistent when:** command, issue filing, prompt and docs agree.
- **Verify on `attesta`:** a fixture repository with a planted bug yields one
  labelled issue; a second run files nothing new.

### F8.1 Spike: runtime, trigger and promotion

- **Depends:** F2 · **Model:** high · **Effort:** ~120k · **Branch after:** consistent (docs only)
- **Context:** `claude-review`, `uat/tester`, Superagent GitHub source (comment triggers and allowlist), `templates/AGENTS.md`.
- **Change:** `docs/adr/0002-bug-hunter.md`: Claude Code or DSH headless; who triggers it; confirm a person's `@` comment on an agent-opened issue starts a ticket.
- **Done when:** the ADR picks one design and records the promotion check.

### F8.2 superagent bug-hunt

- **Depends:** F8.1 · **Model:** mid · **Effort:** ~130k · **Branch after:** inconsistent (no filing)
- **Context:** `scripts/superagent`, `superagent_checks.py`, the ADR, review prompt as a model.
- **Change:** subcommand running the chosen runtime read-only on the default branch; findings as JSON plus a detail file; check contract (0 none, 1 findings).
- **Tests:** fake runtime output → findings; malformed → `3`.
- **Done when:** findings are stable JSON.

### F8.3 File findings as issues

- **Depends:** F8.2 · **Model:** mid · **Effort:** ~90k · **Branch after:** consistent
- **Context:** `scripts/superagent`, `fake_gh.py`, issue template, README.
- **Change:** one issue per finding with a hidden fingerprint, label `bug-hunt`, no mention; skip fingerprints already open; README explains promotion.
- **Tests:** dedupe; body format.
- **Done when:** the verification above passes.

---

## F9 — Planning skills for target repositories

- **Squash:** `feat(templates): ship planning skills for target repositories`
- **Goal:** a person's local agent turns a specification into issues that fit
  the pipeline and the token budget.
- **Skills:** `writing-for-agents`, `domain-modeling`.
- **Consistent when:** skills exist under `templates/`, linked from
  `templates/AGENTS.md` and the README.
- **Verify on `attesta`:** a local agent with the skills opens a sample backlog
  whose issues pass the template checks.

### F9.1 Write-issues skill

- **Depends:** F0 · **Model:** high · **Effort:** ~100k · **Branch after:** consistent
- **Context:** `templates/AGENTS.md`, section 3 of this plan.
- **Change:** `templates/.agents/skills/write-issues/SKILL.md`: split rules, effort line, `Depends on`, UAT section.
- **Done when:** a dry run on a sample spec produces valid issues.

### F9.2 Architect skill

- **Depends:** F9.1 · **Model:** high · **Effort:** ~120k · **Branch after:** consistent
- **Context:** `extending-superagent.md` roles, codebase-memory-mcp README.
- **Change:** `templates/.agents/skills/architect/SKILL.md` (module map, code-graph queries when available, ADRs); README "Planning side".
- **Done when:** linked and loadable by DSH from `.agents/skills`.

---

## F10 — Ticket map in the console

- **Squash:** `feat(console): show the ticket dependency map`
- **Goal:** `/console/map` shows tickets as a graph of `Depends on` edges with
  their stage, live.
- **Skills:** `tdd`.
- **Consistent when:** page, API and docs shipped.
- **Verify on `attesta`:** two linked issues appear connected, coloured by
  stage, and update without reload.

### F10.1 Graph projection

- **Depends:** F7 · **Model:** mid · **Effort:** ~110k · **Branch after:** inconsistent (no page)
- **Context:** Wayfinder Atlas projection code, `scripts/superagent-dependencies` (`Depends on` parsing), orchestrator tickets API.
- **Change:** console server route joining tickets with issue bodies (GitHub read token, `SUPERAGENT_CONSOLE_GITHUB_TOKEN`, falling back to the UAT read token); pure projection to nodes, edges, missing and cyclic dependencies.
- **Tests:** fixture issues → expected graph, including a cycle.
- **Done when:** the route returns the graph for the fixtures.

### F10.2 Map page

- **Depends:** F10.1 · **Model:** mid · **Effort:** ~120k · **Branch after:** consistent
- **Context:** projection types, React Flow and elkjs docs, Wayfinder view as reference.
- **Change:** `/console/map` with elk layout, stage colours, links to issue and PR, polling; "discuss" opens the chat with the ticket as context.
- **Tests:** render test with fixture data.
- **Done when:** the verification above passes.

---

## F11 — `superagent init` *(outline; re-plan after F6)*

- **Squash:** `feat(pkg): scaffold a target repository with superagent init`
- **Goal:** one command adds `AGENTS.md`, `.superagent/uat.yaml` and the
  planning skills to a target repository; runners stay in the deployment.
- **Issues (draft):** F11.1 spike on package shape and what the target
  repository owns (high, ADR); F11.2 `init` writing templates, tested in a
  temp directory; F11.3 `upgrade` syncing skills and writing
  `.superagent/UPGRADE.md` as a work order.

---

## Deferred

| Idea | Why wait |
| ---- | -------- |
| Native DSH tool `superagent_check` | Needs `WORK_TOOLS` changed upstream or a fork |
| Worker `subagent_claude_code` | Wrong contract for gates |
| Executor model per ticket | Needs upstream |
| Mastra or another orchestrator | Overlaps DSH Superagent |
| Multi-user sign-in and multiplayer console | After F10; its own train |
| Console mounted inside target apps (`/_superagent`) | After F11 |
| AI SDK harness driving Claude Code from the console | After F7; never a gate |
| Deployment agent implementing this backlog | After F3, with a feature-branch rule in Delivery |

## Opening the backlog

1. Create the integration branch for the first feature.
2. Open its issues in ID order, so each `Depends on` names an existing issue;
   fill Effort, Model, Context and Branch after from this plan.
3. Run design issues (F5.1, F7.1, F8.1, F9) with Opus first; their ADR or
   brief is the next issue's context.
4. Merge each issue PR into the feature branch; open the feature PR to `attesta`
   when the last issue restores consistency, and squash it with the title in
   section 4.
